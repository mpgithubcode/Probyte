-- Phone numbers get their own field type (validated, shown as a link that dials them). Contacts tables' Phone field
-- becomes one; values synced from address books are kept as they are.
UPDATE fields SET type = 'phone'
 WHERE system_key = 'phone' AND type = 'text' AND table_id IN (SELECT id FROM tables WHERE kind = 'contacts');
