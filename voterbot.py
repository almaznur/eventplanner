import os
import uuid
import logging
import asyncpg
import asyncio
import sys
import threading
from flask import Flask, request, jsonify

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
    MessageHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Set this in Render: https://your-app.onrender.com

if not BOT_TOKEN:
    logger.error("BOT_TOKEN environment variable is not set")
    sys.exit(1)
if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable is not set")
    sys.exit(1)

# Flask app for webhook
flask_app = Flask(__name__)

# Telegram bot application (initialized on startup)
telegram_app: Application | None = None
# Event loop for async operations (shared across threads)
bot_event_loop: asyncio.AbstractEventLoop | None = None

db: asyncpg.Pool | None = None
ADMIN_STATE = {}  # admin_id -> {"event_id": x, "target_user_id": y, "mode": "capacity"}


# ---------- DB ----------

async def init_db():
    global db
    try:
        # Use Supabase connection pooler port (6543) for better performance with webhooks
        # Or use direct connection (5432) - both work
        # IMPORTANT: Disable prepared statements for pgbouncer (Supabase connection pooler)
        # pgbouncer in transaction/statement mode doesn't support prepared statements
        pool_kwargs = {
            'min_size': 1,
            'max_size': 5,
            'statement_cache_size': 0,  # Disable prepared statements for pgbouncer compatibility
        }
        
        # Add SSL if connection string doesn't already include it
        if 'sslmode' not in DATABASE_URL.lower() and 'ssl=' not in DATABASE_URL.lower():
            pool_kwargs['ssl'] = 'require'
        
        logger.info("Creating database connection pool (prepared statements disabled for pgbouncer)...")
        db = await asyncpg.create_pool(DATABASE_URL, **pool_kwargs)
        
        # Test the connection
        async with db.acquire() as conn:
            await conn.fetchval('SELECT 1')
        
        logger.info("Database connection pool created and tested")
    except Exception as e:
        logger.error(f"Failed to create database pool: {e}")
        raise


async def close_db():
    global db
    if db:
        await db.close()
        logger.info("Database connection pool closed")


# ---------- HELPERS ----------

async def is_group_admin(context, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False


async def is_event_admin(context, event, user_id: int) -> bool:
    """Check if user is admin (either creator or group admin)"""
    if user_id == event["created_by"]:
        return True
    return await is_group_admin(context, event["chat_id"], user_id)


async def safe_answer_callback(query, text: str = "", show_alert: bool = False):
    """Safely answer a callback query, handling errors for old/invalid queries"""
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception as e:
        # Don't log BadRequest for old queries - this is expected behavior
        if "too old" not in str(e).lower() and "invalid" not in str(e).lower():
            logger.warning(f"Error answering callback query: {e}")
        # Silently ignore - query might be too old or already answered


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
    try:
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
    except Exception as e:
        logger.error(f"Error rendering event {event_id}: {e}")
        return "❌ Error loading event."


# ---------- COMMANDS ----------

async def create_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        raw = update.message.text.replace("/create", "", 1).strip()
        if not raw:
            raise ValueError("Empty input")
        
        parts = raw.split("|")
        if len(parts) != 2:
            raise ValueError("Invalid format")
        
        title = parts[0].strip()
        max_people = int(parts[1].strip())
        
        if not title:
            raise ValueError("Title cannot be empty")
        if max_people < 1:
            raise ValueError("Max people must be at least 1")
        
    except ValueError as e:
        await update.message.reply_text(
            f"❌ Invalid input: {str(e)}\n\n"
            "Usage:\n/create Event title | max people\n"
            "Example:\n/create Soccer | 12"
        )
        return
    except Exception as e:
        logger.error(f"Error parsing create command: {e}")
        await update.message.reply_text(
            "Usage:\n/create Event title | max people\nExample:\n/create Soccer | 12"
        )
        return

    try:
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
        logger.info(f"Event created: {event_id} by user {update.message.from_user.id}")
    except Exception as e:
        logger.error(f"Error creating event: {e}")
        await update.message.reply_text("❌ Failed to create event. Please try again.")


async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all events, optionally filtered by chat"""
    try:
        # Get events for the current chat, or all events if in private chat
        chat_id = update.message.chat.id
        
        events = await db.fetch(
            """
            select e.*, 
                   coalesce(sum(1 + v.guests), 0) as current_count
            from events e
            left join votes v on e.id = v.event_id
            where e.chat_id = $1
            group by e.id
            order by e.created_at desc
            limit 50
            """,
            chat_id
        )
        
        if not events:
            await update.message.reply_text("📭 No events found in this chat.\n\nCreate one with:\n/create Event Name | Max People")
            return
        
        lines = ["📋 *Events in this chat:*\n"]
        
        for ev in events:
            status = "🟢" if ev["active"] else "🔴"
            lines.append(
                f"{status} *{ev['title']}* (ID: `{ev['id']}`)\n"
                f"   👥 {ev['current_count']}/{ev['max_people']} • "
                f"{'Active' if ev['active'] else 'Closed'}"
            )
        
        text = "\n".join(lines)
        
        # Split if message is too long (Telegram limit is 4096 chars)
        if len(text) > 4000:
            # Send in chunks
            chunk = ""
            for line in lines:
                if len(chunk + line + "\n") > 4000:
                    await update.message.reply_text(chunk, parse_mode="Markdown")
                    chunk = line + "\n"
                else:
                    chunk += line + "\n"
            if chunk:
                await update.message.reply_text(chunk, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
            
    except Exception as e:
        logger.error(f"Error listing events: {e}")
        await update.message.reply_text("❌ Error listing events. Please try again.")


async def cancel_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any ongoing admin action"""
    user_id = update.message.from_user.id
    state = ADMIN_STATE.get(user_id)
    
    if not state:
        await update.message.reply_text("ℹ️ No active action to cancel.")
        return
    
    ADMIN_STATE.pop(user_id, None)
    await update.message.reply_text("❌ Cancelled.")


async def show_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a specific event by ID with management options"""
    try:
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "Usage: /show <event_id>\n"
                "Example: /show 1\n\n"
                "Use /list to see all event IDs."
            )
            return
        
        event_id = int(context.args[0])
        
        ev = await db.fetchrow("select * from events where id=$1", event_id)
        if not ev:
            await update.message.reply_text(f"❌ Event with ID {event_id} not found.")
            return
        
        # Check if user is admin (creator or group admin)
        is_admin = await is_event_admin(context, ev, update.message.from_user.id)
        
        text = await render_event(event_id)
        
        await update.message.reply_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=vote_keyboard(event_id, is_admin, ev["active"]),
        )
        
    except ValueError:
        await update.message.reply_text("❌ Event ID must be a number.\nUsage: /show <event_id>")
    except Exception as e:
        logger.error(f"Error showing event: {e}")
        await update.message.reply_text("❌ Error showing event. Please try again.")


# ---------- VOTING ----------

async def on_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    try:
        _, event_id, value = q.data.split(":")
        event_id = int(event_id)
        user = q.from_user

        ev = await db.fetchrow("select * from events where id=$1", event_id)
        if not ev:
            await safe_answer_callback(q, "❌ Event not found", show_alert=True)
            return
        
        if not ev["active"]:
            await safe_answer_callback(q, "Voting is closed", show_alert=True)
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
                await db.execute(
                    "delete from votes where event_id=$1 and user_id=$2",
                    event_id, user.id
                )
        else:
            guests = int(value)
            new_size = 1 + guests
            old_size = 1 + existing["guests"] if existing else 0

            if current_total - old_size + new_size > ev["max_people"]:
                await safe_answer_callback(q, "❌ Capacity exceeded", show_alert=True)
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

        is_admin = await is_event_admin(context, ev, user.id)

        text = await render_event(event_id)
        await q.edit_message_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=vote_keyboard(event_id, is_admin, ev["active"]),
        )
    except ValueError as e:
        logger.error(f"Error parsing vote callback: {e}")
        await safe_answer_callback(q, "❌ Invalid vote", show_alert=True)
    except Exception as e:
        logger.error(f"Error processing vote: {e}")
        await safe_answer_callback(q, "❌ Error processing vote", show_alert=True)


# ---------- ADMIN ----------

async def admin_manage(update, context, event_id):
    try:
        q = update.callback_query
        logger.info(f"Admin manage called for event {event_id} by user {q.from_user.id}")
        
        votes = await db.fetch(
            "select user_id, user_name from votes where event_id=$1",
            event_id
        )

        if not votes:
            await safe_answer_callback(q, "No votes to manage yet. Ask users to vote using the buttons first.", show_alert=True)
            return

        buttons = [
            [InlineKeyboardButton(v["user_name"], callback_data=f"au:{event_id}:{v['user_id']}")]
            for v in votes
        ]

        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="au:cancel")])

        await q.edit_message_text(
            "Select user to edit:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        logger.info(f"Admin manage menu shown for event {event_id}")
    except Exception as e:
        logger.error(f"Error in admin_manage: {e}", exc_info=True)
        await safe_answer_callback(update.callback_query, f"❌ Error: {str(e)[:50]}", show_alert=True)


async def on_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    try:
        data = q.data.split(":")
        prefix = data[0]
        logger.info(f"Admin callback received: prefix={prefix}, data={q.data}")

        if prefix == "a":
            _, event_id, action = data
            event_id = int(event_id)
            logger.info(f"Processing admin action '{action}' for event {event_id}")

            ev = await db.fetchrow("select * from events where id=$1", event_id)
            if not ev:
                await safe_answer_callback(q, "❌ Event not found", show_alert=True)
                return

            # Check if user is event creator OR group admin
            if not await is_event_admin(context, ev, q.from_user.id):
                await safe_answer_callback(q, "Admins only", show_alert=True)
                logger.warning(f"User {q.from_user.id} tried to manage event {event_id} but is not admin/creator")
                return

            if action == "manage":
                logger.info(f"Processing manage action for event {event_id}")
                await admin_manage(update, context, event_id)

            elif action == "close":
                await db.execute("update events set active=false where id=$1", event_id)
                text = await render_event(event_id)
                is_admin = await is_event_admin(context, ev, q.from_user.id)
                await q.edit_message_text(
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=vote_keyboard(event_id, is_admin, False),
                )

            elif action == "delete":
                await db.execute("delete from events where id=$1", event_id)
                await q.edit_message_text("🗑 Event deleted")
                logger.info(f"Event {event_id} deleted by user {q.from_user.id}")
                return

            elif action == "capacity":
                logger.info(f"Capacity button pressed for event {event_id} by user {q.from_user.id}")
                ADMIN_STATE[q.from_user.id] = {"event_id": event_id, "mode": "capacity"}
                try:
                    # Try to reply to the message (works in groups/chats)
                    await q.message.reply_text("📝 Reply with new max capacity (must be at least 1):")
                except Exception as e:
                    # If reply fails (e.g., inline query message), send a new message
                    logger.warning(f"Could not reply to message, sending new message: {e}")
                    try:
                        await context.bot.send_message(
                            chat_id=q.from_user.id,
                            text="📝 Reply with new max capacity (must be at least 1):"
                        )
                    except Exception as e2:
                        logger.error(f"Could not send message to user: {e2}")
                        await safe_answer_callback(q, "❌ Could not send message. Please try again.", show_alert=True)

        elif prefix == "au":
            # Handle cancel button (data format: "au:cancel")
            if len(data) == 2 and data[1] == "cancel":
                try:
                    await q.message.delete()
                except Exception:
                    # If delete fails, just edit the message back
                    try:
                        ev = await db.fetchrow("select * from events where id=$1", ADMIN_STATE.get(q.from_user.id, {}).get("event_id"))
                        if ev:
                            text = await render_event(ev["id"])
                            is_admin = await is_event_admin(context, ev, q.from_user.id)
                            await q.edit_message_text(
                                text=text,
                                parse_mode="Markdown",
                                reply_markup=vote_keyboard(ev["id"], is_admin, ev["active"]),
                            )
                    except Exception:
                        pass
                ADMIN_STATE.pop(q.from_user.id, None)
                return
            
            # Handle user selection (data format: "au:event_id:user_id")
            if len(data) != 3:
                logger.error(f"Invalid au callback data format: {data}")
                await safe_answer_callback(q, "❌ Invalid action", show_alert=True)
                return
            
            _, event_id, user_id = data

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
                [InlineKeyboardButton("❌ Cancel", callback_data="au:cancel")],
            ]

            await q.edit_message_text(
                "Choose vote:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

        elif prefix == "av":
            admin_id = q.from_user.id
            state = ADMIN_STATE.pop(admin_id, None)
            if not state or "target_user_id" not in state:
                await safe_answer_callback(q, "❌ Session expired", show_alert=True)
                return

            event_id = state["event_id"]
            target_user_id = state["target_user_id"]
            value = data[1]

            ev = await db.fetchrow("select * from events where id=$1", event_id)
            if not ev:
                await safe_answer_callback(q, "❌ Event not found", show_alert=True)
                return

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
            is_admin = await is_event_admin(context, ev, admin_id)
            await q.edit_message_text(
                text=text,
                parse_mode="Markdown",
                reply_markup=vote_keyboard(event_id, is_admin, ev["active"]),
            )
            logger.info(f"Admin {admin_id} edited vote for user {target_user_id} in event {event_id}")

    except ValueError as e:
        logger.error(f"Error parsing admin callback: {e}")
        await safe_answer_callback(q, "❌ Invalid action", show_alert=True)
    except Exception as e:
        logger.error(f"Error in admin action: {e}")
        await safe_answer_callback(q, "❌ Error processing action", show_alert=True)


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin replies for capacity updates and adding users"""
    user_id = update.message.from_user.id
    state = ADMIN_STATE.get(user_id)
    
    if not state:
        return
    
    mode = state.get("mode")
    
    # Handle capacity updates
    if mode == "capacity":
        await handle_capacity_update(update, context, state, user_id)
        return


async def handle_capacity_update(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict, user_id: int):
    """Handle capacity update replies"""
    try:
        event_id = state["event_id"]
        new_max = int(update.message.text.strip())
        
        if new_max < 1:
            await update.message.reply_text("❌ Capacity must be at least 1. Please try again:")
            return
        
        current_total = await db.fetchval(
            "select coalesce(sum(1 + guests),0) from votes where event_id=$1",
            event_id
        )
        
        if new_max < current_total:
            await update.message.reply_text(
                f"❌ New capacity ({new_max}) is less than current attendees ({current_total}). "
                "Please remove some votes first or set a higher capacity:"
            )
            return
        
        await db.execute(
            "update events set max_people=$1 where id=$2",
            new_max, event_id
        )
        
        ADMIN_STATE.pop(user_id, None)
        
        # Send confirmation message first
        await update.message.reply_text(f"✅ Capacity updated to {new_max}")
        
        # Then show the updated event
        ev = await db.fetchrow("select * from events where id=$1", event_id)
        if ev:
            text = await render_event(event_id)
            is_admin = await is_event_admin(context, ev, user_id)
            
            # Try to update the original event message if it's a reply
            if update.message.reply_to_message:
                try:
                    await update.message.reply_to_message.edit_text(
                        text=text,
                        parse_mode="Markdown",
                        reply_markup=vote_keyboard(event_id, is_admin, ev["active"]),
                    )
                except Exception:
                    # If edit fails, send as new message
                    await update.message.reply_text(
                        text=text,
                        parse_mode="Markdown",
                        reply_markup=vote_keyboard(event_id, is_admin, ev["active"]),
                    )
            else:
                # Send updated event as new message
                await update.message.reply_text(
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=vote_keyboard(event_id, is_admin, ev["active"]),
                )
            
            logger.info(f"Event {event_id} capacity updated to {new_max} by user {user_id}")
        
    except ValueError:
        await update.message.reply_text("❌ Please send a valid number (must be at least 1):")
    except Exception as e:
        logger.error(f"Error updating capacity: {e}")
        ADMIN_STATE.pop(user_id, None)
        await update.message.reply_text("❌ Error updating capacity. Please try again.")


# Removed handle_add_user function - users can only be added via voting buttons


# ---------- INLINE ----------

async def inline_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    iq = update.inline_query
    q = iq.query.strip()

    try:
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
    except Exception as e:
        logger.error(f"Error in inline query: {e}")
        await iq.answer([])


# ---------- FLASK WEBHOOK ROUTES ----------

@flask_app.route('/', methods=['GET'])
def health():
    """Health check endpoint - Render pings this to keep service alive"""
    return jsonify({"status": "ok", "bot": "running"}), 200


@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook from Telegram"""
    if request.method == "POST":
        try:
            json_data = request.get_json(force=True)
            update = Update.de_json(json_data, telegram_app.bot)
            
            # Process update in the bot's event loop using run_coroutine_threadsafe
            if bot_event_loop and bot_event_loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    telegram_app.process_update(update),
                    bot_event_loop
                )
                # Don't wait for completion to return quickly to Telegram
                # Errors will be logged by the application's error handlers
            else:
                # Fallback: run in new event loop if main loop not available
                def process_update_async():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(telegram_app.process_update(update))
                    except Exception as e:
                        logger.error(f"Error in async processing: {e}")
                    finally:
                        loop.close()
                
                thread = threading.Thread(target=process_update_async)
                thread.daemon = True
                thread.start()
            
            return "ok", 200
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Method not allowed"}), 405


@flask_app.route('/setwebhook', methods=['GET', 'POST'])
def set_webhook():
    """Set webhook URL (call this once after deployment)"""
    try:
        if not WEBHOOK_URL:
            return jsonify({"error": "WEBHOOK_URL not set"}), 500
        
        webhook_url = f"{WEBHOOK_URL}/webhook"
        result = asyncio.run(telegram_app.bot.set_webhook(webhook_url))
        return jsonify({"status": "ok", "webhook_url": webhook_url, "result": result}), 200
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")
        return jsonify({"error": str(e)}), 500


@flask_app.route('/deletewebhook', methods=['GET', 'POST'])
def delete_webhook():
    """Delete webhook (switch back to polling)"""
    try:
        result = asyncio.run(telegram_app.bot.delete_webhook())
        return jsonify({"status": "ok", "result": result}), 200
    except Exception as e:
        logger.error(f"Error deleting webhook: {e}")
        return jsonify({"error": str(e)}), 500


# ---------- INITIALIZATION ----------

async def init_telegram_app():
    """Initialize the Telegram bot application"""
    global telegram_app
    
    await init_db()
    
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    
    telegram_app.add_handler(CommandHandler("create", create_event))
    telegram_app.add_handler(CommandHandler("list", list_events))
    telegram_app.add_handler(CommandHandler("events", list_events))  # Alias for /list
    telegram_app.add_handler(CommandHandler("show", show_event))
    telegram_app.add_handler(CommandHandler("cancel", cancel_admin_action))
    telegram_app.add_handler(CallbackQueryHandler(on_vote, pattern="^v:"))
    telegram_app.add_handler(CallbackQueryHandler(on_admin, pattern="^(a:|au:|av:)"))
    telegram_app.add_handler(InlineQueryHandler(inline_events))
    telegram_app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_admin_reply
    ))
    
    await telegram_app.initialize()
    logger.info("Telegram bot initialized")
    return telegram_app


# ---------- MAIN ----------

def run_event_loop(loop):
    """Run the event loop in a background thread"""
    asyncio.set_event_loop(loop)
    loop.run_forever()


if __name__ == '__main__':
    # Initialize bot on startup
    logger.info("Initializing bot...")
    
    try:
        # Create and set event loop (bot_event_loop is already module-level)
        bot_event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_event_loop)
        
        # Initialize bot in the event loop
        bot_event_loop.run_until_complete(init_telegram_app())
        logger.info("Bot initialized successfully")
        
        # Start event loop in background thread
        loop_thread = threading.Thread(target=run_event_loop, args=(bot_event_loop,), daemon=True)
        loop_thread.start()
        logger.info("Event loop running in background thread")
        
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
        logger.error("Please check:")
        logger.error("1. DATABASE_URL is set correctly")
        logger.error("2. Supabase connection string format: postgresql://postgres:[PASSWORD]@db.[PROJECT].supabase.co:5432/postgres")
        logger.error("3. For connection pooler: postgresql://postgres.[PROJECT]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres")
        sys.exit(1)
    
    # Run Flask app
    # Render will set PORT environment variable
    port = int(os.getenv("PORT", 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False)
