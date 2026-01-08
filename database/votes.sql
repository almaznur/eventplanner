create table if not exists votes (
  event_id bigint references events(id) on delete cascade,
  user_id bigint not null,
  user_name text not null,
  guests int not null default 0,
  updated_at timestamptz default now(),
  primary key (event_id, user_id)
);