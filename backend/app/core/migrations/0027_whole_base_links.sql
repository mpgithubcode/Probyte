-- A public link can share a whole base: every table and combined database in it, also ones added later.
ALTER TABLE shares ADD COLUMN IF NOT EXISTS whole_base boolean NOT NULL DEFAULT false;
-- A base made from such a link on the other side: where it comes from, whether its views stay updated, and which
-- linked table stands for which shared table ({"url", "token", "local", "cookie"?, "follow_views", "tables": {item id: table id}}).
ALTER TABLE bases ADD COLUMN IF NOT EXISTS linked jsonb;
