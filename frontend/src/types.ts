export type FieldType =
  | "text" | "long_text" | "recurrence" | "record_id" | "number" | "checkbox" | "single_select" | "multi_select" | "date" | "url" | "email" | "rating"
  | "user" | "attachment" | "link"
  | "formula" | "lookup" | "rollup" | "conditional_lookup" | "conditional_rollup" | "button" | "autonumber"
  | "created_time" | "last_modified_time" | "created_by" | "last_modified_by";

export type ValueType = "number" | "text" | "boolean" | "date" | "array" | "any";

export interface FilterCond { field: string; op: string; value?: unknown; valueField?: string }

export interface Attachment { id: string; name: string; mime: string; size: number; url?: string }

export interface Refs { records: Record<string, string>; users: Record<string, string> }

export interface Choice { name: string; color: string }

export interface FieldOptions {
  choices?: Choice[];
  includeTime?: boolean;
  max?: number;
  // link
  foreignTableId?: string;
  relationship?: "one" | "many";
  symmetricFieldId?: string;
  symmetric?: boolean;
  // lookup / rollup / conditional
  linkFieldId?: string;
  lookupFieldId?: string;
  func?: string;
  filters?: FilterCond[];
  sortField?: string | null;
  sortDir?: "asc" | "desc";
  limit?: number | null;
  // formula
  expression?: string;
  displayExpression?: string;
  resultType?: ValueType;
  precision?: number;
  // button
  label?: string;
  color?: string;
  action?: "url" | "update";
  url?: string;
  updates?: { fieldId: string; value: unknown }[];
  // user
  multiple?: boolean;
  // filled in by the server
  _inner?: { type: FieldType; options: FieldOptions; name: string };
  _error?: string;
}

export interface Field {
  id: string;
  name: string;
  type: FieldType;
  options: FieldOptions;
  system_key?: string | null;
  is_primary?: boolean;
  readonly?: boolean;
  computed?: boolean;
  mapping?: Record<string, string>; // combined databases: table_id -> source field id
}

export interface Rec {
  id: string;
  table_id: string;
  data: Record<string, any>;
  sync_status?: "synced" | "dirty";
  occurrence?: string;     // a repeat of the record laid out by the calendar/timeline: its start
  occurrenceOf?: string;   // the record's own start (the first occurrence)
  overridden?: boolean;    // this occurrence was changed on its own
  created_at?: string;
}

export type ViewType = "grid" | "kanban" | "timeline" | "calendar" | "list" | "gallery" | "chart";
export type FilterOp = "contains" | "not_contains" | "is" | "is_not" | "empty" | "not_empty" | "gt" | "gte" | "lt" | "lte";

export interface Filter { field: string; op: FilterOp; value?: string }
/** Conditions joined by "and"/"or"; items can be conditions or nested groups. */
export interface FilterGroup { conjunction: "and" | "or"; items: (Filter | FilterGroup)[] }
export interface SortSpec { field: string; dir: "asc" | "desc"; hideTitle?: boolean; noArrow?: boolean }  // the last two: group levels

export interface ViewConfig {
  hidden?: string[];
  shown?: string[];   // fields hidden by default (Record ID) that this view shows
  sort?: { field: string; dir: "asc" | "desc" } | null;   // older views: one sort (see sorts)
  sorts?: SortSpec[];
  filters?: Filter[];                                     // older views: AND-ed conditions (see filter)
  filter?: FilterGroup;
  groupBy?: string;                                       // Board columns (and older grouped views)
  groups?: SortSpec[];                                    // Table / List / Gallery: up to 3 group levels
  hideEmptyColumns?: boolean;                             // Board
  hideUnassigned?: boolean;                               // Board: hide the "No <field>" column
  hideBlankCards?: boolean;                               // Board: hide cards with no title and no values
  listProps?: { field: string; align: "left" | "center" | "right" }[];  // List: property order and placement
  listLabels?: boolean;                                   // List: show property names (default on)
  calMode?: "month" | "week" | "day";                     // Calendar
  dateField?: string;
  endField?: string | null;
  colorField?: string | null;
  scale?: "day" | "week" | "month";   // timeline
  coverField?: string | null;         // gallery: field holding an image URL
  chart?: ChartConfig;
}

export interface ChartConfig {
  type: "bar" | "line" | "donut";
  xField?: string;
  agg: "count" | "sum" | "avg";
  yField?: string;
  bucket: "day" | "week" | "month";  // grouping for date fields
  hideEmpty?: boolean;
}

export interface View { id: string; name: string; type: ViewType; config: ViewConfig }

export type TableKind = "generic" | "tasks" | "events" | "mixed" | "contacts" | "remote";

/** Where a linked table's data comes from (a Probyte public link, on this or another server). */
export interface RemoteInfo {
  url: string; origin: string; local: boolean; name: string | null;
  permissions: { add?: boolean; edit?: boolean; delete?: boolean }; error: string | null; has_password: boolean;
}

export interface Base { id: string; name: string; color: string; position: number }

export interface TableInfo {
  id: string;
  base_id: string;
  name: string;
  kind: TableKind;
  account_id: string | null;
  calendar_url: string | null;
  last_synced_at: string | null;
  last_sync_error: string | null;
  remote?: RemoteInfo | null;
}

export interface Account {
  id: string;
  name: string;
  url: string;
  carddav_url: string | null;
  username: string | null;
  has_password: boolean;
  is_builtin: boolean;
}

export interface User { id: string; username: string; is_admin: boolean }

export interface DavInfo {
  enabled: boolean;
  provisioned: boolean;
  public_url: string | null;
  username: string | null;
  password: string | null;
}

export interface ComboSummary { id: string; base_id: string; name: string; sources: { table_id: string; color: string }[] }

export interface LinkSource { table_id: string; name: string; color?: string | null; kind?: string }
export interface Source { table_id: string; name: string; kind: TableKind; color: string; link_sources?: LinkSource[] | null }

/** Where a new record goes: a combined database's source table, and/or a table inside a linked combined database. */
export interface CreateTarget { label: string; color?: string | null; tableId?: string; linkTableId?: string }

export interface Workspace { bases: Base[]; tables: TableInfo[]; combos: ComboSummary[]; accounts: Account[] }

export interface SharePerms { add: boolean; edit: boolean; delete: boolean }
export interface ShareItem { kind: "table" | "combo"; id: string; views: string[] | null }
export interface Share {
  id: string; base_id: string; token: string; name: string; items: ShareItem[]; permissions: SharePerms;
  expires_at: string | null; enabled: boolean; color: string; has_password: boolean; created_at: string;
}
export interface ApiKey {
  id: string; name: string; prefix: string; scope: "read" | "write"; base_ids: string[] | null;
  expires_at: string | null; last_used_at: string | null; created_at: string; key?: string;
  source?: "manual" | "oauth";   // oauth: created when an app (e.g. an AI assistant) was connected
}

export interface CalendarInfo { url: string; name: string; components: string[] }

/** A table or a combined database, loaded for display. */
export interface Dataset {
  kind: "table" | "combo";
  id: string;
  name: string;
  table?: TableInfo;
  sources?: Source[];
  comboFields?: { id: string; name: string; mapping: Record<string, string> }[];
  linkSources?: LinkSource[] | null;  // linked table pointing at a combined database: its tables
  dav?: { account: string | null; builtin: boolean; shared: boolean; items: number; collection: "calendar" | "address book" } | null;
  baseId?: string;
  fields: Field[];
  views: View[];
  records: Rec[];
  refs: Refs;
}
