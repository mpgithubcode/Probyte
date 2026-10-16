export type FieldType =
  | "text" | "long_text" | "recurrence" | "record_id" | "number" | "checkbox" | "single_select" | "multi_select" | "date" | "url" | "email" | "phone" | "rating"
  | "user" | "attachment" | "link"
  | "formula" | "lookup" | "rollup" | "conditional_lookup" | "conditional_rollup" | "button" | "autonumber"
  | "created_time" | "last_modified_time" | "created_by" | "last_modified_by";

export type ValueType = "number" | "text" | "boolean" | "date" | "array" | "any";

export interface FilterCond { field: string; op: string; value?: unknown; valueField?: string }

export interface Attachment {
  id: string; name: string; mime: string; size: number; url?: string;
  file?: boolean;   // a file of a files table (S3 / WebDAV): id is its record's id
  thumb?: boolean;  // it has a small preview image
  previewed?: string;       // files tables: a preview was tried for this version
  preview_error?: string;   // …and why there's none
  preview_retry?: boolean;  // …and it's tried again (the converter or the storage couldn't be reached)
  missing?: boolean;        // files tables: listed by the storage, but not there when read
  view?: "image" | "pdf" | "video" | "audio" | "text" | null;  // how the browser can show it
}

/** A saved S3 / WebDAV connection for files tables. */
export interface Storage {
  id: string; name: string; kind: "s3" | "webdav"; endpoint: string; region: string; bucket: string; username: string;
  path_style: boolean; has_secret: boolean; tables?: number;
}

/** Titles and names that values refer to. display: link fields that show another field of their records than the
 * title (the field, and its value for each linked record). */
export interface Refs {
  records: Record<string, string>; users: Record<string, string>;
  display?: Record<string, { field: Field; values: Record<string, any> }>;
  chips?: Record<string, { fields: Field[]; values: Record<string, Record<string, any>> }>;   // link field id -> its chips' extra fields
}

/** How a link field's linked records look as chips, everywhere it's shown (cells, the record, pages, boards, links). */
export type ChipStyle = "chip" | "compact" | "details" | "card" | "plain";
export interface ChipLook { style?: ChipStyle; fields?: string[]; colorField?: string | null; imageField?: string | null }

export interface Choice { name: string; color: string; from?: string }  // from: the name it had when the field editor opened

export interface FieldOptions {
  renames?: Record<string, string>;  // sent when saving: select options renamed in the editor (old name -> new name)
  choices?: Choice[];
  includeTime?: boolean;
  dateFormat?: "local" | "long" | "iso" | "us" | "eu";
  timeFormat?: "12" | "24";
  max?: number;
  // number
  numberFormat?: "decimal" | "percent" | "currency" | "bytes";
  currency?: string;
  separators?: boolean;
  // single / multi select: false = only the listed options can be chosen
  allowNew?: boolean;
  _filesTable?: boolean;  // link: the linked table is a files table (sent by the server)
  // link
  foreignTableId?: string;
  relationship?: "one" | "many";
  displayFieldId?: string | null;  // link: the field of the linked records shown in the cell (none: their title)
  displayFiles?: boolean;          // link / lookup: files shown as preview thumbnails (default) or by name
  chip?: ChipLook | null;          // link: how its records' chips look (fields shown in them, a colour, a picture)
  pickFields?: string[];           // link: the columns its picker shows of the linked table (none set: the first few)
  views?: FieldViews;              // link / lookup / rollup: how it's shown in the table's cells and on the record
  pickView?: string | null;          // Link: the picker offers the records of this view of the linked table…
  pickFilter?: FilterGroup | null;   // …and / or those matching these conditions
  stampWhen?: string | null;      // Date: fill in the date when this formula is true (Started date, Completed date…)
  stampClear?: boolean;           // …and clear it when the formula is no longer true
  stampKind?: "started" | "completed" | null;
  displayStampWhen?: string;
  doneField?: string | false | null;   // Repeat (ordinary tables): the checkbox that completes it (false: none; unset: the only one)
  dateFields?: string[] | null;        // Repeat: the dates that move to the next date (unset: all)
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
  realName?: string;  // in a view that renames it: the field's own name
  id: string;
  name: string;
  type: FieldType;
  options: FieldOptions;
  system_key?: string | null;
  is_primary?: boolean;
  readonly?: boolean;
  computed?: boolean;
  mapping?: Record<string, string>; // combined databases: table_id -> source field id
  description?: string | null;
  required?: boolean;
  unique_values?: boolean;
  default_value?: unknown;
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
export type DateGrouping = "day" | "week" | "month" | "year" | "relative";
export interface SortSpec { field: string; dir: "asc" | "desc"; hideTitle?: boolean; noArrow?: boolean;  // the last two: group levels
  dateBy?: DateGrouping }  // group levels on a date: by day (default), week, month, year, or relative to today

export type ListAlign = "start" | "left" | "center" | "right";

export interface ViewConfig {
  hidden?: string[];
  shown?: string[];   // fields hidden by default (Record ID) that this view shows
  sorts?: SortSpec[];
  filter?: FilterGroup;
  groupBy?: string;                                       // Board: the field whose options are the columns
  groups?: SortSpec[];                                    // Table / List / Gallery: up to 3 group levels
  hideEmptyColumns?: boolean;                             // Board
  hideUnassigned?: boolean;                               // Board: hide the "No <field>" column
  hideBlankCards?: boolean;                               // Board: hide cards with no title and no values
  columnOrder?: string[];                                 // Board: the columns' order (option names; "__none": the "No …" column)
  // List: property order, placement (start = right after the color dot, before the title) and whether its name shows
  listProps?: { field: string; align: ListAlign; label?: boolean }[];
  fieldOrder?: string[];
  pageSize?: number;
  fieldNames?: Record<string, string>;  // names fields go by in this view only (the field keeps its own)
  columnWidths?: Record<string, number>;  // table view: column widths dragged by hand (field id -> px)
  summary?: Record<string, string>;     // table view footer: field id -> what to show (sum, avg, filled…)
  frozen?: number;  // table view: how many columns (after the row numbers) stay put when scrolling sideways  // table / list / gallery: records per page (unset = all)  // column order dragged in the table view (also used for cards)
  listLabels?: boolean;                                   // List: show property names (default on)
  calMode?: "month" | "week" | "day";                     // Calendar
  dateField?: string;
  endField?: string | null;
  colorField?: string | null;
  scale?: "day" | "week" | "month";   // timeline
  coverField?: string | null;         // gallery: field holding an image URL
  density?: Density;                  // table / list / board / gallery: how much room rows and cards take
  recordSections?: RecordSection[];   // the record panel: fields grouped under headings (the rest at the top)
  chart?: ChartConfig;
}

export type Density = "compact" | "comfortable" | "roomy";
export interface RecordSection { name: string; fields: string[] }

export interface ChartConfig {
  type: "bar" | "line" | "donut";
  xField?: string;
  agg: "count" | "sum" | "avg";
  yField?: string;
  bucket: "day" | "week" | "month";  // grouping for date fields
  hideEmpty?: boolean;
}

export interface View { id: string; name: string; type: ViewType; config: ViewConfig }

/** A way to show a field's value (which fit which field type: display.ts; backend/app/data/pages.py): its chips, a count,
 * plain text, a list, a table / cards / cards in a row of linked records (their `fields`), its value, a bar (`max` is a
 * full bar), a big number, stars or a number, a tick or Yes / No, a relative date, file thumbnails / a gallery / names,
 * large text. */
export type FieldView = "chips" | "count" | "text" | "list" | "table" | "cards" | "hcards" | "value" | "bar" | "stat" | "stars"
  | "number" | "check" | "yesno" | "relative" | "thumbs" | "gallery" | "names" | "large";
/** A field's own choice, in the table (cells, cards, lists) and on its records (the record panel and pages). */
export interface FieldViews { cell?: FieldView; record?: FieldView; fields?: string[]; max?: number }

/** A table's page: records open laid out like a document, in blocks (backend/app/data/pages.py). A block spans 1 to 4
 * of the page's columns (unset: all 4), can have a fixed height (px; its content scrolls), and can show only for records
 * that match its condition (`when`). Sections and tabs hold blocks of their own. */
export type PageBlockType = "properties" | "field" | "heading" | "divider" | "doc" | "section" | "tabs" | "related" | "files" | "stats";
export interface PageTab { id: string; text: string; blocks: PageBlock[] }
export interface PageBlock {
  id: string; type: PageBlockType; span?: number; height?: number; when?: FilterGroup;
  field?: string; view?: FieldView;                 // field, doc (a long text as the page's body), files
  text?: string;                                    // heading, section's title, related list's title
  closed?: boolean; blocks?: PageBlock[];           // section (starts folded)
  tabs?: PageTab[];                                 // tabs
  table?: string; filterView?: string; to?: string;   // related (to: a combined database's source table it's for): records of `table` linking here through its `field`, in a view's
  display?: "list" | "table" | "cards"; fields?: string[]; limit?: number;   // filter and order; shown as a list, table or cards
  items?: { field: string; view: "stat" | "bar" }[];   // stats
}
export interface PageLayout {
  enabled: boolean; blocks: PageBlock[];
  header?: string[];          // fields under the title, kept in view with it while scrolling
  outline?: boolean;          // a list of the page's headings, sections and tabs to jump to
  template?: string;          // a new record's page body starts as this (Markdown)
  templateField?: string;
  variants?: { field: string; layouts: Record<string, PageBlock[]> };   // a layout per kind of record (a single select's options)
}

export type TableKind = "generic" | "tasks" | "events" | "mixed" | "contacts" | "remote" | "files";

/** Where a linked table's data comes from (a Probyte public link, on this or another server). */
export interface RemoteInfo {
  url: string; origin: string; local: boolean; name: string | null;
  source_kind?: TableKind | "combo" | null;  // what the shared table is (files, contacts, tasks & events…): it behaves like one
  permissions: LinkPerms; error: string | null; has_password: boolean;
  follow_views?: boolean;  // its view stays in step with the source's view
}

export type Role = "owner" | "admin" | "editor" | "viewer";
/** A base: the user's own (role owner) or one shared with them (owner: whose it is). */
export interface Base { id: string; name: string; color: string; position: number; role?: Role; follow_views?: boolean; owner?: string | null;
  linked?: LinkedBaseInfo | null }   // made from someone's public link to a whole base
export interface LinkedBaseInfo { url: string; name: string | null; follow_views: boolean; error: string | null; checked_at: number | null; tables: number; local?: boolean }
/** What the user may do in a table's / combined database's base. */
export interface Access { role: Role; follow_views: boolean; owner: string | null }

export interface TableInfo {
  id: string;
  base_id: string;
  name: string;
  hidden?: boolean;  // left out of the sidebar
  icon?: string | null;  // an emoji instead of the default icon
  kind: TableKind;
  account_id: string | null;
  calendar_url: string | null;
  last_synced_at: string | null;
  last_sync_error: string | null;
  remote?: RemoteInfo | null;
  description?: string | null;
  storage_id?: string | null;     // files tables
  storage_prefix?: string | null;
  page?: PageLayout | null;       // records open as pages
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

export interface Prefs { week_start?: number }  // 0 = Sunday … 6 = Saturday
export interface User { id: string; username: string; is_admin: boolean; prefs?: Prefs }

export interface DavInfo {
  enabled: boolean;
  provisioned: boolean;
  public_url: string | null;
  username: string | null;
  password: string | null;
}

export interface ComboSummary { id: string; base_id: string; name: string; sources: { table_id: string; color: string }[]; hidden?: boolean; icon?: string | null }

export interface LinkSource { table_id: string; name: string; color?: string | null; kind?: string }
export interface Source { table_id: string; name: string; kind: TableKind; color: string; link_sources?: LinkSource[] | null }

/** Where a new record goes: a combined database's source table, and/or a table inside a linked combined database. */
export interface CreateTarget { label: string; color?: string | null; tableId?: string; linkTableId?: string }

export interface Workspace { bases: Base[]; tables: TableInfo[]; combos: ComboSummary[]; accounts: Account[] }

// upload_files / edit_files: files tables (upload; replace, edit text, edit in ONLYOFFICE); null = the same as add / edit
export interface SharePerms { add: boolean; edit: boolean; delete: boolean; upload_files?: boolean | null; edit_files?: boolean | null }
/** What a link allows, as its server spells it out. */
export interface LinkPerms { add?: boolean; edit?: boolean; delete?: boolean; upload_files?: boolean; edit_files?: boolean }
export interface ShareItem { kind: "table" | "combo"; id: string; views: string[] | null }
export interface Share {
  id: string; base_id: string; token: string; name: string; items: ShareItem[]; permissions: SharePerms; whole_base?: boolean;
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
  // a calendar / contacts table: on Probyte's own server (builtin, with its path there) or synced with an account's server
  dav?: { account: string | null; builtin: boolean; shared: boolean; items: number; collection: "calendar" | "address book"; path?: string } | null;
  storage?: { name: string | null; kind: "s3" | "webdav" | null; location: string | null } | null;  // files tables
  baseId?: string;
  access?: Access | null;  // the user's role in its base (a shared base: not "owner")
  fields: Field[];
  views: View[];
  records: Rec[];
  refs: Refs;
  token?: string | null;  // where these records stand, for fetching just the changes (tables only)
  loadedAt?: number;      // when it was last loaded in full
  pending?: string[];     // combined databases: linked-table sources still loading
  icon?: string | null;   // combined databases: an emoji (tables have it in `table`)
  description?: string | null;  // combined databases (tables have it in `table`)
  page?: PageLayout | null;     // combined databases: records as pages (tables have it in `table`)
}
