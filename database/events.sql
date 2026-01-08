create table if not exists events (
  id bigserial primary key,
  chat_id bigint not null,
  title text not null,
  max_people int not null,
  created_by bigint not null,
  active boolean default true,
  created_at timestamptz default now()
);