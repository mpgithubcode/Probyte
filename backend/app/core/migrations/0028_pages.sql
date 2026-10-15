-- A table's page: records open as a page (like a document) laid out with blocks of their fields, instead of the record
-- panel ({"enabled": bool, "blocks": [{"id", "type": field | doc | heading | divider | properties, "field"?, "view"?,
-- "width"?: full | half, "text"?}]}). NULL: no page.
ALTER TABLE tables ADD COLUMN IF NOT EXISTS page jsonb;
