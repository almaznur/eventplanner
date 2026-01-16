-- Add message_id column to events table for tracking original event messages
-- This allows the bot to update the original event message in group chats
-- when admins make changes from private chats

alter table events add column if not exists message_id bigint;
