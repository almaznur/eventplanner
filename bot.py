from telegram.ext import Application, CommandHandler

TOKEN = "YOUR_BOT_TOKEN"

# Dictionary to manage multiple event lists
event_lists = {}  # Format: {"event_name": {"players": [], "total_count": 0, "max_players": 12}}

DEFAULT_MAX_PLAYERS = 12  # Default maximum number of players for any event

# Helper function to check if the user is an admin
async def is_admin(update):
    chat_member = await update.effective_chat.get_member(update.effective_user.id)
    return chat_member.status in ['administrator', 'creator']

# Command to create a new event (Admin Only)
async def create(update, context):

    if not await is_admin(update):
        await update.message.reply_text("Only group admins can create events.")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Please specify the event name. Optionally, you can set a maximum number of players. Example: /create SoccerGame_2024-12-20_4PM 15")
        return

    event_name = " ".join(context.args[:-1]) if len(context.args) > 1 else " ".join(context.args)
    try:
        max_players = int(context.args[-1]) if len(context.args) > 1 and context.args[-1].isdigit() else DEFAULT_MAX_PLAYERS
    except ValueError:
        await update.message.reply_text("The maximum number of players must be a number. Example: /create SoccerGame_2024-12-20_4PM 15")
        return

    if event_name in event_lists:
        await update.message.reply_text(f"An event list with the name '{event_name}' already exists.")
        return

    event_lists[event_name] = {"players": [], "total_count": 0, "max_players": max_players}
    await update.message.reply_text(f"Event list '{event_name}' has been created with a maximum of {max_players} players.")

# Command to join an event
async def join(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Please specify the event name and the number of additional players. Example: /join SoccerGame_2024-12-20_4PM +2")
        return

    event_name = context.args[0]
    additional = int(context.args[1].replace("+", "")) if context.args[1].startswith("+") else 0

    if event_name not in event_lists:
        await update.message.reply_text(f"No event found with the name '{event_name}'.")
        return

    name = update.effective_user.first_name
    user_id = update.effective_user.id  # Store user's unique ID
    event = event_lists[event_name]

    if name in [p["name"] for p in event["players"]]:
        await update.message.reply_text(f"{name}, you are already in the list for '{event_name}'.")
        return

    new_total = event["total_count"] + 1 + additional
    if new_total > event["max_players"]:
        await update.message.reply_text(f"Adding {name} and {additional} additional player(s) exceeds the limit ({event['max_players']}).")
        return

    event["players"].append({"name": name, "user_id": user_id, "additional": additional})
    event["total_count"] = new_total
    await update.message.reply_text(
        f"{name} joined the event '{event_name}' with {additional} additional player(s)! Total: {event['total_count']}/{event['max_players']}."
    )

# Command to delete a user’s own entry
async def delete(update, context):
    if len(context.args) < 1:
        await update.message.reply_text("Please specify the event name. Example: /delete SoccerGame_2024-12-20_4PM")
        return

    event_name = context.args[0]
    if event_name not in event_lists:
        await update.message.reply_text(f"No event found with the name '{event_name}'.")
        return

    name_to_delete = update.effective_user.first_name
    user_id_to_delete = update.effective_user.id
    event = event_lists[event_name]

    for player in event["players"]:
        if player["name"] == name_to_delete and player.get("user_id") == user_id_to_delete:
            event["total_count"] -= 1 + player["additional"]
            event["players"].remove(player)
            await update.message.reply_text(
                f"{name_to_delete}, you have been removed from the event '{event_name}' along with your {player['additional']} additional player(s). Total: {event['total_count']}/{event['max_players']}."
            )
            return

    await update.message.reply_text("You can only delete your own entry or you are not in the list.")

# Command to update a user’s own additional players
async def update(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Please specify the event name and the new additional player count. Example: /update SoccerGame_2024-12-20_4PM +2")
        return

    event_name = context.args[0]
    if event_name not in event_lists:
        await update.message.reply_text(f"No event found with the name '{event_name}'.")
        return

    name_to_update = update.effective_user.first_name
    user_id_to_update = update.effective_user.id
    try:
        new_additional = int(context.args[1].replace("+", ""))
    except ValueError:
        await update.message.reply_text("The additional player count must be a number. Example: /update SoccerGame_2024-12-20_4PM +2")
        return

    event = event_lists[event_name]
    for player in event["players"]:
        if player["name"] == name_to_update and player.get("user_id") == user_id_to_update:
            event["total_count"] -= player["additional"]
            event["total_count"] += new_additional
            if event["total_count"] > event["max_players"]:
                event["total_count"] -= new_additional
                event["total_count"] += player["additional"]
                await update.message.reply_text(
                    f"Updating your additional players exceeds the limit ({event['max_players']}) for '{event_name}'."
                )
                return

            player["additional"] = new_additional
            await update.message.reply_text(
                f"Your additional players have been updated to {new_additional} for '{event_name}'. Total: {event['total_count']}/{event['max_players']}."
            )
            return

    await update.message.reply_text("You can only update your own entry or you are not in the list.")

# Command to view all events
async def events(update, context):
    if not event_lists:
        await update.message.reply_text("No events have been created yet.")
        return

    event_names = "\n".join(f"{event_name} (Max Players: {event['max_players']})" for event_name, event in event_lists.items())
    await update.message.reply_text(f"Current events:\n{event_names}")

# Command to view the players in an event
async def show(update, context):
    if len(context.args) < 1:
        await update.message.reply_text("Please specify the event name. Example: /show SoccerGame_2024-12-20_4PM")
        return

    event_name = context.args[0]
    if event_name not in event_lists:
        await update.message.reply_text(f"No event found with the name '{event_name}'.")
        return

    event = event_lists[event_name]
    if not event["players"]:
        await update.message.reply_text(f"No players have joined the event '{event_name}'.")
        return

    player_list = "\n".join(
        f"{i + 1}. {p['name']} (+{p['additional']})" for i, p in enumerate(event["players"])
    )
    await update.message.reply_text(
        f"Players for '{event_name}' ({event['total_count']}/{event['max_players']}):\n{player_list}"
    )

# Admin-only commands
async def update_max(update, context):
    if not await is_admin(update):
        await update.message.reply_text("Only group admins can update the maximum players.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Please specify the event name and the new maximum number of players. Example: /update_max SoccerGame_2024-12-20_4PM 15")
        return

    event_name = " ".join(context.args[:-1])
    try:
        new_max = int(context.args[-1])
    except ValueError:
        await update.message.reply_text("The maximum number of players must be a number. Example: /update_max SoccerGame_2024-12-20_4PM 15")
        return

    if event_name not in event_lists:
        await update.message.reply_text(f"No event found with the name '{event_name}'.")
        return

    event_lists[event_name]["max_players"] = new_max
    await update.message.reply_text(f"The maximum number of players for '{event_name}' has been updated to {new_max}.")

async def delete_event(update, context):
    if not await is_admin(update):
        await update.message.reply_text("Only group admins can delete events.")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Please specify the event name. Example: /delete_event SoccerGame_2024-12-20_4PM")
        return

    event_name = " ".join(context.args)
    if event_name not in event_lists:
        await update.message.reply_text(f"No event found with the name '{event_name}'.")
        return

    del event_lists[event_name]
    await update.message.reply_text(f"The event '{event_name}' has been deleted.")

async def admin_delete(update, context):
    if not await is_admin(update):
        await update.message.reply_text("Only group admins can delete users from events.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Please specify the event name and the user's name. Example: /admin_delete SoccerGame_2024-12-20_4PM John")
        return

    event_name = context.args[0]
    user_name = " ".join(context.args[1:])

    if event_name not in event_lists:
        await update.message.reply_text(f"No event found with the name '{event_name}'.")
        return

    event = event_lists[event_name]
    for player in event["players"]:
        if player["name"] == user_name:
            event["total_count"] -= 1 + player["additional"]
            event["players"].remove(player)
            await update.message.reply_text(f"{user_name} has been removed from the event '{event_name}'. Total: {event['total_count']}/{event['max_players']}.")
            return

    await update.message.reply_text(f"No user found with the name '{user_name}' in the event '{event_name}'.")

# Command for admin to add players by name to an event
async def admin_add(update, context):
    # Check if the user is an admin
    if not await is_admin(update):
        await update.message.reply_text("Only group admins can add players by name to events.")
        return

    # Validate input arguments
    if len(context.args) < 3:
        await update.message.reply_text("Please specify the event name, the player's name, and the number of additional players. Example: /admin_add SoccerGame_2024-12-20_4PM John +2")
        return

    event_name = context.args[0]  # Extract event name
    player_name = context.args[1]  # Extract player name
    try:
        additional = int(context.args[2].replace("+", ""))  # Extract additional players
    except ValueError:
        await update.message.reply_text("The number of additional players must be a valid number. Example: /admin_add SoccerGame_2024-12-20_4PM John +2")
        return

    # Check if the event exists
    if event_name not in event_lists:
        await update.message.reply_text(f"No event found with the name '{event_name}'.")
        return

    # Add the player to the event
    event = event_lists[event_name]
    if player_name in [p["name"] for p in event["players"]]:
        await update.message.reply_text(f"{player_name} is already in the list for '{event_name}'.")
        return

    # Calculate the new total players
    new_total = event["total_count"] + 1 + additional
    if new_total > event["max_players"]:
        await update.message.reply_text(f"Adding {player_name} with {additional} additional player(s) exceeds the limit ({event['max_players']}).")
        return

    # Add the player
    event["players"].append({"name": player_name, "user_id": None, "additional": additional})
    event["total_count"] = new_total
    await update.message.reply_text(
        f"{player_name} has been added to the event '{event_name}' with {additional} additional player(s). Total: {event['total_count']}/{event['max_players']}."
    )

# Main function to run the bot
def main():
    app = Application.builder().token(TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("update", update))
    app.add_handler(CommandHandler("events", events))
    app.add_handler(CommandHandler("show", show))

    # Admin commands
    app.add_handler(CommandHandler("create", create))
    app.add_handler(CommandHandler("update_max", update_max))
    app.add_handler(CommandHandler("delete_event", delete_event))
    app.add_handler(CommandHandler("admin_delete", admin_delete))
    app.add_handler(CommandHandler("admin_add", admin_add))

    # Start the bot
    app.run_polling()

if __name__ == "__main__":
    main()
