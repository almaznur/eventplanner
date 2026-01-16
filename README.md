# Event Planner Bot

A Telegram bot for planning events with voting functionality. Users can create events, vote on attendance, and bring guests.

## Features

- ✅ Create events with capacity limits
- 👥 Vote on events (IN, OUT, or bring guests)
- 🧑‍🤝‍🧑 Admin controls to manage votes and events
- 📱 Inline queries to search and share events
- 💾 PostgreSQL database (Supabase) for persistence

## Commands

### `/create Event Name | Max People`
Create a new event with a maximum capacity.

**Examples:**
- `/create Soccer Game | 12`
- `/create Birthday Party | 20`
- `/create Team Meeting | 10`

### `/list` or `/events`
List all events in the current chat.

Shows:
- Event title and ID
- Current attendance count vs. capacity
- Active/Closed status

**Example:**
- `/list`
- `/events`

### `/show <event_id>`
View a specific event by ID.

Shows the event with voting buttons.

**Example:**
- `/show 1`
- `/show 5`

## Voting

After creating an event, use the inline buttons to vote:
- **✅ IN** - You're attending
- **👤 +1** - You + 1 guest
- **👤 +2** - You + 2 guests
- **👤 +3** - You + 3 guests
- **👤 +4** - You + 4 guests
- **❌ OUT** - Remove your vote

## Inline Queries

Type `@YourBotName` in any chat:
- Type an event ID to find a specific event: `@YourBotName 1`
- Leave blank to see recent active events: `@YourBotName`

## Admin Features

If you're a group admin or event creator, use these commands to manage events:

### `/capacity <event_id> [new_capacity]`
Change the maximum capacity of an event.

**Examples:**
- `/capacity 1 20` - Set capacity to 20
- `/capacity 1` - Reply to the bot's message with the new capacity

### `/manage <event_id>`
Manage individual user votes. Shows a list of users who voted, allowing you to edit their votes.

**Example:**
- `/manage 1`

### `/close <event_id>`
Close voting for an event (users can no longer vote).

**Example:**
- `/close 1`

### `/delete <event_id>`
Permanently delete an event and all its votes.

**Example:**
- `/delete 1`

⚠️ **Note:** All admin commands require you to be either the event creator or a group admin.

## Setup

1. Set environment variables:
   - `BOT_TOKEN` - Your Telegram bot token from @BotFather
   - `DATABASE_URL` - Supabase PostgreSQL connection string
   - `WEBHOOK_URL` - Your Render service URL (e.g., `https://your-app.onrender.com`)

2. Deploy to Render (or similar platform)

3. Set webhook: Visit `https://your-app.onrender.com/setwebhook`

## Database Schema

See `database/events.sql` and `database/votes.sql` for table definitions.
