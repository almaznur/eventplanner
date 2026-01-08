import os
import uuid
import logging
import asyncpg

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

db: asyncpg.Pool | None = None
ADMIN_STATE = {}  # admin_id -> {"event_id": x, "target_user_id": y}


# ---------- DB ----------

async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)


# ---------- HELPERS ----------

async def is_group_admin(context, chat_id: int, user_id: int) -> bool:
    member = await context.bot.get_chat_member(chat_id, user_id)
    return member.status in ("administrator", "creator")


# ---------- UI ----------

def vote_keyboard(event_id: int, is_admin: bool, is_active: bool):
    rows = []

    if is_active:
        rows.append([InlineKeyboardButton("✅ IN", callback_data=f"v:{event_id}:0")])
        rows.append([
            InlineKeyboardButton("👤 +1", callback_data=f"v:{event_id}:1"),
            InlineKeyboardButton("👥 +2", callback_data=f"v:{event_id}:2"),
            InlineKeyboardButton("👥👤 +3", callback_data=f"v:{event_id}:3"),
            InlineKeyboardButton("👥👥👤 +4", callback_data=f"v:{event_id}:4"),
        ])
        rows.append([InlineKeyboardButton("❌ OUT", callback_data=f"v:{event_id}:out")])

    if is_admin:
        rows.append([
            InlineKeyboardButton("🧑‍🤝‍🧑 Manage votes", callback_data=f"a:{event_id}:manage"),
        ])
        rows.append([
            InlineKeyboardButton("⚙️ Capacity", callback_data=f"a:{event_id}:capacity"),
            InlineKeyboardButton("🔒 Close", callback_data=f"a:{event_id}:close"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"a:{event_id}:delete"),
        ])

    return InlineKeyboardMarkup(rows)


# ---------- RENDER ----------

async def render_event(event_id: int) -> str:
    ev = await db.fetchrow("select * from events where id=$1", event_id)
    if not ev:
        return "❌ Event not found."

    votes = await db.fetch(
        "select user_id, user_name, guests from votes where event_id=$1 order by updated_at",
        event_id,
    )

    total = sum(1 + v["guests"] for v in votes)

    lines = [
        f"📌 *{ev['title']}*",
        f"👥 {total}/{ev['max_people']}",
        "",
    ]

    for v in votes:
        label = "IN" if v["guests"] == 0 else f"+{v['guests']}"
        lines.append(f"• {v['user_name']} ({label})")

    lines.append(f"\n🆔 Event ID: `{event_id}`")
    return "\n".join(lines)


# ---------- COMMANDS ----------

async def create_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw = update.message.text.replace("/create", "", 1).strip()
        title, max_people = raw.split("|")
        title = title.strip()
        max_people = int(max_people.strip())
    except Exception:
        await update.message.reply_text(
            "Usage:\n/create Event title | max people\nExample:\n/create Soccer | 12"
        )
        return

    row = await db.fetchrow(
        """
        insert into events (chat_id, title, max_people, created_by)
        values ($1,$2,$3,$4)
        returning id
        """,
        update.message.chat.id,
        title,
        max_people,
        update.message.from_user.id,
    )

    event_id = row["id"]
    text = await render_event(event_id)

    await update.message.reply_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=vote_keyboard(event_id, True, True),
    )


# ---------- VOTING ----------

async def on_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    _, event_id, value = q.data.split(":")
    event_id = int(event_id)
    user = q.from_user

    ev = await db.fetchrow("select * from events where id=$1", event_id)
    if not ev or not ev["active"]:
        await q.answer("Voting is closed", show_alert=True)
        return

    existing = await db.fetchrow(
        "select guests from votes where event_id=$1 and user_id=$2",
        event_id, user.id
    )

    current_total = await db.fetchval(
        "select coalesce(sum(1 + guests),0) from votes where event_id=$1",
        event_id
    )

    if value == "out":
        if existing:
            current_total -= (1 + existing["guests"])
            await db.execute(
                "delete from votes where event_id=$1 and user_id=$2",
                event_id, user.id
            )
    else:
        guests = int(value)
        new_size = 1 + guests
        old_size = 1 + existing["guests"] if existing else 0

        if current_total - old_size + new_size > ev["max_people"]:
            await q.answer("❌ Capacity exceeded", show_alert=True)
            return

        await db.execute(
            """
            insert into votes (event_id, user_id, user_name, guests)
            values ($1,$2,$3,$4)
            on conflict (event_id, user_id)
            do update set guests=$4, updated_at=now()
            """,
            event_id, user.id, user.full_name, guests
        )

    is_admin = (
        user.id == ev["created_by"]
        or await is_group_admin(context, ev["chat_id"], user.id)
    )

    text = await render_event(event_id)
    await q.edit_message_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=vote_keyboard(event_id, is_admin, ev["active"]),
    )


# ---------- ADMIN ----------

async def admin_manage(update, context, event_id):
    votes = await db.fetch(
        "select user_id, user_name from votes where event_id=$1",
        event_id
    )

    buttons = [
        [InlineKeyboardButton(v["user_name"], callback_data=f"au:{event_id}:{v['user_id']}")]
        for v in votes
    ]

    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="au:cancel")])

    await update.callback_query.edit_message_text(
        "Select user to edit:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def on_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data.split(":")
    prefix = data[0]

    if prefix == "a":
        _, event_id, action = data
        event_id = int(event_id)

        ev = await db.fetchrow("select * from events where id=$1", event_id)
        if not ev:
            return

        if not await is_group_admin(context, ev["chat_id"], q.from_user.id):
            await q.answer("Admins only", show_alert=True)
            return

        if action == "manage":
            await admin_manage(update, context, event_id)

        elif action == "close":
            await db.execute("update events set active=false where id=$1", event_id)

        elif action == "delete":
            await db.execute("delete from events where id=$1", event_id)
            await q.edit_message_text("🗑 Event deleted")
            return

        elif action == "capacity":
            ADMIN_STATE[q.from_user.id] = {"event_id": event_id, "mode": "capacity"}
            await q.message.reply_text("Reply with new max capacity:")

    elif prefix == "au":
        _, event_id, user_id = data
        if user_id == "cancel":
            await q.message.delete()
            return

        ADMIN_STATE[q.from_user.id] = {
            "event_id": int(event_id),
            "target_user_id": int(user_id),
        }

        buttons = [
            [InlineKeyboardButton("✅ IN", callback_data="av:0")],
            [
                InlineKeyboardButton("👤 +1", callback_data="av:1"),
                InlineKeyboardButton("👥 +2", callback_data="av:2"),
            ],
            [
                InlineKeyboardButton("👥👤 +3", callback_data="av:3"),
                InlineKeyboardButton("👥👥👤 +4", callback_data="av:4"),
            ],
            [InlineKeyboardButton("❌ OUT", callback_data="av:out")],
        ]

        await q.edit_message_text(
            "Choose vote:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif prefix == "av":
        admin_id = q.from_user.id
        state = ADMIN_STATE.pop(admin_id, None)
        if not state:
            return

        event_id = state["event_id"]
        target_user_id = state["target_user_id"]
        value = data[1]

        if value == "out":
            await db.execute(
                "delete from votes where event_id=$1 and user_id=$2",
                event_id, target_user_id
            )
        else:
            guests = int(value)
            await db.execute(
                "update votes set guests=$3, updated_at=now() where event_id=$1 and user_id=$2",
                event_id, target_user_id, guests
            )

        text = await render_event(event_id)
        await q.edit_message_text(text=text, parse_mode="Markdown")


async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = ADMIN_STATE.get(update.message.from_user.id)
    if not state:
        return

    if state.get("mode") == "capacity":
        event_id = state["event_id"]
        new_max = int(update.message.text.strip())
        await db.execute(
            "update events set max_people=$1 where id=$2",
            new_max, event_id
        )
        ADMIN_STATE.pop(update.message.from_user.id, None)
        await update.message.reply_text("✅ Capacity updated")


# ---------- INLINE ----------

async def inline_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    iq = update.inline_query
    q = iq.query.strip()

    if q.isdigit():
        events = await db.fetch("select * from events where id=$1", int(q))
    else:
        events = await db.fetch(
            "select * from events where active=true order by created_at desc limit 10"
        )

    results = []
    for ev in events:
        text = await render_event(ev["id"])
        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=ev["title"],
                description=f"Event #{ev['id']}",
                input_message_content=InputTextMessageContent(
                    text=text, parse_mode="Markdown"
                ),
                reply_markup=vote_keyboard(ev["id"], False, ev["active"]),
            )
        )

    await iq.answer(results, cache_time=1, is_personal=True)


# ---------- MAIN ----------

async def main():
    await init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("create", create_event))
    app.add_handler(CallbackQueryHandler(on_vote, pattern="^v:"))
    app.add_handler(CallbackQueryHandler(on_admin, pattern="^(a:|au:|av:)"))
    app.add_handler(InlineQueryHandler(inline_events))
    app.add_handler(CommandHandler("text", admin_text))

    await app.run_polling()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
