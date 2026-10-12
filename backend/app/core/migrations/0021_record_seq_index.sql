-- Every new record gets the table's next number (MAX(seq) + 1): an index makes that one lookup instead of a scan of
-- the table, so adding a record costs the same in a table of 50 rows and one of 50,000 (and an import doesn't slow
-- down as it goes).
CREATE INDEX IF NOT EXISTS records_table_seq ON records (table_id, seq);
