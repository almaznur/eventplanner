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

## Voting

After creating an event, use the inline buttons to vote:
- **✅ IN** - You're attending
- **👤 +1** - You + 1 guest
- **👥 +2** - You + 2 guests
- **👥👤 +3** - You + 3 guests
- **👥👥👤 +4** - You + 4 guests
- **❌ OUT** - Remove your vote

## Inline Queries

Type `@YourBotName` in any chat:
- Type an event ID to find a specific event: `@YourBotName 1`
- Leave blank to see recent active events: `@YourBotName`

## Admin Features

If you're a group admin or event creator, you'll see additional buttons:
- **🧑‍🤝‍🧑 Manage votes** - Edit individual user votes
- **⚙️ Capacity** - Change max capacity (reply with new number)
- **🔒 Close** - Close voting for the event
- **🗑 Delete** - Delete the event

## Setup

1. Set environment variables:
   - `BOT_TOKEN` - Your Telegram bot token from @BotFather
   - `DATABASE_URL` - Supabase PostgreSQL connection string
   - `WEBHOOK_URL` - Your Render service URL (e.g., `https://your-app.onrender.com`)

2. Deploy to Render (or similar platform)

3. Set webhook: Visit `https://your-app.onrender.com/setwebhook`

## Database Schema

See `database/events.sql` and `database/votes.sql` for table definitions.
