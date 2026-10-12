-- ONLYOFFICE editors for files of linked tables (table_id is then the linked table, record_id the source's id, which
-- comes from another server) and for old formats edited as their newer one (convert_from: .doc → .docx…).
ALTER TABLE office_sessions ALTER COLUMN record_id TYPE text USING record_id::text;
ALTER TABLE office_sessions ADD COLUMN IF NOT EXISTS linked boolean NOT NULL DEFAULT false;
ALTER TABLE office_sessions ADD COLUMN IF NOT EXISTS file_type text;      -- what the editor edits (docx for a .doc)
ALTER TABLE office_sessions ADD COLUMN IF NOT EXISTS convert_from text;   -- the file's own format, when converted
