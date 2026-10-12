"""The record store: bases, tables, fields, views and records, and the rules every write follows (field types,
required / unique values, defaults, links kept two-way, what tasks and events do when their status changes).

Every record write ends in events.record_changed (app/core/events.py); the store doesn't know who listens.
Its public interface is this module; the web API, REST v1, MCP, public links, imports, the DAV server and the sync
all write through it.

    schema.py     bases, tables, fields, views
    rules.py      required / unique / default
    behaviors.py  what tasks and events do on their own
    records.py    record writes, links, history, change feed
    combos.py     combined databases
"""
from app.core.db import Jsonb  # noqa: F401
from app.formats.ical import PALETTE  # noqa: F401
from app.data.schema import (  # noqa: F401
    DAV_KINDS, PRIMARY_TYPES, SELECT_TYPES, bad, base_allowed, check_field_name,
    compile_formula, create_field, create_link_pair, create_table, create_view, ensure_base,
    ensure_choices, ensure_system_fields, get_base, get_fields, get_table, get_views,
    is_remote_table, is_served, is_synced, not_found, now_iso, prepare_field,
    rename_choices, unique_field_name,
)
from app.data.rules import (  # noqa: F401
    DEFAULT_TYPES, RULE_TYPES, UNIQUE_TYPES, apply_defaults, check_rules, field_rules,
    is_blank,
)
from app.data.behaviors import (  # noqa: F401
    advance_recurring, apply_done, apply_event_defaults, apply_task_auto, sync_done,
)
from app.data.records import (  # noqa: F401
    _changed, _sync_links, clean_record, computed_record, create_record, delete_field_cleanup,
    delete_record, insert_record, list_records, normalize_data, restore_record, update_record,
)
from app.data.combos import (  # noqa: F401
    NAME_MEANINGS, _project, build_combo_fields, combo_records, combo_refs, get_combo,
    project_record, resolve_combo, unified_to_source,
)
