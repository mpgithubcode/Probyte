# Probyte

A small, self-hosted, multi-user, [Teable](https://teable.ai/)-style database app backed by PostgreSQL. Each user's work is organised as **bases → tables**. A base groups related tables (normal tables, CalDAV task/event tables) and combined databases. Use the sidebar to create, rename, recolour or delete bases, and a table's ⋯ menu to move it to another base.

On top of that, Probyte has these extras:

1. **CalDAV task & event databases.** A database can be bound to a calendar collection and sync both ways with any CalDAV server (Nextcloud, Radicale, Baïkal, iCloud, Fastmail…). There are three kinds:
   - *tasks* syncs `VTODO`s
   - *events* syncs `VEVENT`s
   - *tasks & events* syncs a calendar that holds both. Each record has a **Type** field (Task / Event).

   Each user can connect as many CalDAV accounts as they like.

   **Repeating tasks and events.** Tasks and events have a **Repeat** field (daily / weekly on chosen days / monthly on a date or the "2nd Tuesday" / yearly, every N, ending never / on a date / after N times). The calendar and timeline show every occurrence. Open one to **change that date only** (its title, start and end) or **skip it**, or drag it to another day on the calendar; edits in the rest of the panel apply to the whole series. Occurrences moved, renamed or cancelled on other devices show where they really are. Completing a repeating task moves it to its next date and keeps a completed copy of the finished one, like Apple Reminders. The Repeat field type can be added to any table too.

   **Contacts** tables work the same way over **CardDAV**: a table bound to an address book syncs vCards both ways (name, company, job title, email, phone, address, birthday, website, notes, groups). The same accounts are used; for providers with a separate contacts address (iCloud: `https://contacts.icloud.com/`), set the optional *Contacts (CardDAV) URL* on the account.
2. **Combined databases.** You can show several databases together as one: for example, tasks and events on a single calendar. Fields are lined up through a mapping (Title ↔ Title, Due ↔ End, Tags ↔ Tags…), and edits made in the combined view are written back to the source database.

3. **A built-in CalDAV server for phones.** Probyte ships with Radicale and creates a login on it for every user. A user adds that login on their iPhone, and their Probyte calendars show up in the Calendar app (events) and the Reminders app (tasks). Added as a CardDAV account too, their Contacts tables show up in the Contacts app.

Every database and combined database can have any number of Notion-style views, each with its own filters, sort, hidden fields and grouping:

| View | What it shows |
|---|---|
| **Table** | Spreadsheet grid; click a cell to edit it |
| **Board** | Kanban columns grouped by a single-select field; drag cards between columns |
| **Timeline** | Records as bars between a start and end date. Drag a bar to move it, or drag its edges to change the start or end. Day, week and month scales. Click an empty row to give an undated record dates. |
| **Calendar** | **Month**, **Week** and **Day**. Month: drag items to another day. Week/Day: a time grid with an all-day row (on phones the week scrolls sideways and opens on today); click a slot to add, drag a block to move it (15-minute steps, across days in Week), drag its bottom edge to change the end |
| **List** | One line per record: the title, then properties shown as *name value*. **Properties** sets their order and puts each on the left, center or right, and can hide the names. Every row uses the same columns, so values line up straight; long values end in "…" and empty ones leave their spot blank |
| **Gallery** | Cards, with an optional cover image taken from a URL or text field |
| **Chart** | Bar, line or donut chart. Group by any field (dates by day, week or month) and show a count, sum or average of a number field. A **Data** toggle shows the underlying table. |

**Filters, sorts and groups (like Teable).** **Filter** builds conditions joined by *and* / *or*, with nested **condition groups** (e.g. *Type is Event* **or** (*Status is In Progress* **and** *Type is Task*)); numbers and dates have greater/less than, before/after. A condition without a value is ignored until you fill it in. **Sort** takes several levels ("by Type, then by Title Z → A"). In Table, List and Gallery views, **Group** nests up to three levels (e.g. Type → Status); groups follow the field's option order (dates and numbers ascending, empty last), collapse with a click, and a group's **+** adds a record with that group's values. The same filters and sorts apply on public links and in the API's `view=`.

**Group headers.** Each group level has two switches in **Group**: **Title** (show that level's header row, e.g. "Event" / "Task", or let its records simply follow each other) and **Collapse arrow** (whether the header can fold its group away).

**Adding records.** Every view has a **New** button left of the search box (also on public links that allow adding).

**Dragging on touch screens.** On phones and tablets an item is picked up only after holding it still for **3 seconds** (it vibrates, then follows your finger), so swiping over it scrolls normally and a tap opens it. This applies to Week/Day calendar blocks, Timeline bars, Board cards and Month calendar items. Board cards also have a **Move to** button.

**Board options.** The **Board** button on a Board view shows or hides the "No *Status*" column (cards with no value), hides empty columns, and hides empty cards (no title and no other values).

**Combining tables from a table.** A table's **⋯ → Combine with other tables…** starts a combined database with that table already picked; a combined database's **⋯ → Add tables…** adds more sources.

**Selecting rows.** In a Table view, tick rows (shift-click selects a range, a group's box selects the group, the header box selects everything in the view), then **Edit** to set one field on all of them or **Delete** them.

**Import and export.** A table's **⋯ → Import from file…** reads CSV, TSV, Excel (.xlsx, .xls), OpenDocument (.ods), Numbers and JSON. You pick which column goes into which field, into a new field (type guessed from the data), or nowhere; values are converted to the field's type and rows are added. **⋯ → Export…** saves the current view (its filters, sort and fields, optionally the hidden ones too) as .xlsx, .csv, .tsv, .ods, .xls or .json.

**IDs.** Bases, tables, combined databases and records all have UUIDs. **Copy base ID** (base menu) and **Copy table ID** (table ⋯ menu) put them on the clipboard. Every table has a **Record ID** column with each row's UUID; it's hidden in every view by default — turn it on under **Fields**. The same IDs are used by the API (`/api/tables/{id}`, `/api/records/{id}`).

## Public links, embeds and phone apps

**Settings → Public links** lists your links, where you can create, edit, switch off, or delete them. Each link has these settings:

- a **base** and which of its tables and combined databases to show, plus **which views** of each
- what visitors can do: **view**, **add**, **edit** or **delete** records
- an optional **password** and **expiry date**, a name and a colour

A link works three ways:

| | |
|---|---|
| Web page | `https://your-probyte/s/<token>` |
| Embed | `<iframe src="https://your-probyte/s/<token>?embed=1">`. The code is in the link editor. |
| Phone app (PWA) | Open the link, then choose **Add to Home Screen** (iPhone) or **Install app** (Android). It installs with the link's own name, icon and colour. |

Visitors receive **only the fields visible in the shared views** and **only records that match each view's filters**. Filtering happens on the server, so nothing else reaches the browser. Link, user and attachment fields are read-only for visitors. "New URL" replaces a link's address and the old one stops working.

The main app installs the same way, as "Probyte", and works on phones: the sidebar becomes a menu drawer and dialogs open full screen.

## Linked tables (from any Probyte server)

Any Probyte public link, whether it's from this server or another Probyte server, can be added to one of your bases as a **linked table**. Choose **+ New table → From a link**, paste the link, enter its password if it has one, and pick which shared table and view to show.

- The data is **live**. It's fetched through the link (cached for a few seconds), so you always see the owner's current records, including their formula and lookup results.
- **The link owner's settings apply.** You see only the fields and records the link shares. You can add, edit or delete only what the owner allowed, and edits go straight into the owner's table. If the owner switches the link off, deletes it or changes its password, the linked table empties and shows why. **⋯ → Link settings** lets you re-enter a password or paste a new link.
- You can make **your own views** of a linked table (Table, Board, Calendar, Chart…). Its fields come from the source, so you can't add fields to it.
- **Merge** linked tables with your own tables in a **combined database**. For example, show a supplier's shared stock table next to your own orders, or a colleague's calendar next to yours. Each table's title column lines up automatically.
- **Link to them.** A *Link to record* field in your own table can point at a linked table's records, so you can pick them, and look up or roll up their values. The link is one-way, since the linked table's fields belong to its owner. If the owner deletes or stops sharing a record, it disappears from your links.
- **Linked combined databases:** if the link shares a combined database with several tables, **New record** asks which of the owner's tables the record goes into. The same choice appears, as "Name › Table", when that linked table is part of one of your own combined databases.
- Links to this same server are read directly. Links to other servers are fetched by this server, which doesn't follow redirects and **refuses private/internal network addresses**. If your Probyte servers talk to each other over a LAN or VPN, set `REMOTE_ALLOW_PRIVATE=true`.

## API, MCP and webhooks

Everything is under **Settings → API & MCP**.

**API keys** are **read-only** or **read & write**, cover **all bases or chosen ones**, and can expire. A key is shown once (Probyte stores only its hash). Send it as `Authorization: Bearer pbk_…` or `X-API-Key: pbk_…`. Keys work with data only, never with your password, CalDAV accounts, public links or other keys.

### REST API (`/api/v1`)

A stable API for scripts and automation tools (n8n, Make, Zapier, Home Assistant, GPT Actions). Interactive docs: `/api/v1/docs`; OpenAPI spec (with this server's address, ready to import): `/api/v1/openapi.json`.

- Tables by **id or name** (`?base=` if two bases share a name); fields keyed by **name**.
- Values written as **plain text**: `"2026-10-12"` / `"2026-10-12T15:30:00Z"` for dates, option names for selects, lists or `"a, b"` for multiple selects, record **titles** or ids for links, usernames for user fields. Unknown fields and unreadable values give a clear `400`.
- `GET …/records` supports `filter` (`{"Status": "Done"}` or `[{"field": "Due", "op": "lt", "value": "2026-11-01"}]`; ops `is, is_not, contains, not_contains, gt, gte, lt, lte, empty, not_empty`), `search`, `sort=-Due,Name`, `view`, `fields`, `limit` (≤ 1000) / `offset` with `next_offset`, and `cell_format=text` for values as shown in the app.
- Batch create / update / delete (up to 1000, all-or-nothing) and **upsert** (`match_on: ["Email"]`).
- Schema: list bases, describe a table, create tables and fields; trigger a CalDAV sync; search all tables.

```bash
curl -G -H "Authorization: Bearer $KEY" https://your-probyte/api/v1/tables/Tasks/records \
     --data-urlencode 'filter={"Status": "To Do"}' -d sort=-Due
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"fields": {"Title": "Call the plumber", "Due": "2026-10-20"}}' https://your-probyte/api/v1/tables/Tasks/records
```

The older `/api/...` endpoints the web app uses keep working with keys too.

### MCP server (`/mcp`)

AI assistants can use Probyte through the Model Context Protocol: list bases, describe tables, list/search/get records, create, update, upsert and delete records, create tables and fields, and sync calendars. A read-only connection only gets the read tools.

- **Streamable HTTP** at `https://your-probyte/mcp` (current clients) and **HTTP+SSE** at `/mcp/sse` (older ones).
- **Sign-in (OAuth 2.1)** with dynamic client registration and PKCE, so Claude (claude.ai, desktop, mobile), ChatGPT connectors, Claude Code, Cursor, VS Code and others connect with just the URL. You approve the app on a Probyte page, choosing read-only or read & write and which bases; the connection becomes an API key labelled "connected app" that you can revoke.
- Or use a **bearer token**: **Create MCP token** in Settings makes a key to send as `Authorization: Bearer …`, or set a fixed `MCP_BEARER_TOKEN` (acting as `MCP_BEARER_USER`) in the environment. Any API key works the same way (n8n's MCP Client Tool, LibreChat, …), put the key in the URL (`/mcp?key=pbk_…`) for apps that accept only a URL, or bridge stdio-only apps with `npx -y mcp-remote https://your-probyte/mcp --header "Authorization: Bearer pbk_…"`.

Settings shows step-by-step instructions for each of these.

### Webhooks

Probyte POSTs `record.created`, `record.updated` and `record.deleted` events (with the record's fields by name) to your URL whenever records change, whether from the app, the API, an assistant, a public link or a phone sync. Pick the events and tables; changes to one record within a second become one event. Deliveries go through the worker's job queue: they survive restarts, and failures (no answer, HTTP 5xx or 429) are retried three times with backoff. Each request carries `X-Probyte-Signature: sha256=<HMAC-SHA256 of the body>` using the webhook's secret. Webhooks can also be managed through the API (`/api/v1/webhooks`). Addresses on your own network need `WEBHOOK_ALLOW_PRIVATE=true`.

## Field types

| Group | Types |
|---|---|
| Basic | Single line text, Long text, Number, Single select, Multiple select, User, Date, Rating, Checkbox, Attachment, URL, Email |
| Advanced | Formula, Link to record, Lookup, Rollup, Conditional lookup, Conditional rollup, Button, Auto number |
| System | Created time, Last modified time, Created by, Last modified by |

- **Link to record** connects records across tables, or within one table. By default it is two-way: adding a link also creates the matching field in the other table, and the two stay in sync. Deleting a record removes it from every link that points to it.
- **Lookup** shows a field from the linked records. **Rollup** summarises it: count, sum, average, min, max, and/or/xor, join, unique.
- **Conditional lookup** and **Conditional rollup** don't need a link field. They pick records from any table that match conditions. A condition can compare against a fixed value or against a field of the current record, for example "Project contains {Name}". They can also sort and limit the matches.
- **Formula** works like Teable's:
  - Fields are referenced as `{Field name}`.
  - Operators are `+ - * / %`, `&`, `= != < > <= >=` and `&& || !`.
  - About 80 functions are available across the Numeric, Text, Logical, Date, Array and System groups. For example `IF`, `SWITCH`, `ROUND`, `REGEXP_REPLACE`, `DATE_ADD`, `DATETIME_DIFF`, `DATETIME_FORMAT`, `WORKDAY`, `ARRAY_JOIN` and `RECORD_ID`.
  - The editor has autocomplete, syntax highlighting, a searchable list of fields and functions with docs, and live validation with a preview on real records.
  - Formulas are stored by field ID, so renaming a field never breaks them. A formula that depends on itself, directly or through other fields, is rejected.
  - Date functions follow Teable exactly. `DATETIME_DIFF(date1, date2, [unit])` returns *date1 − date2*, defaulting to seconds; days and hours can be fractional. `FROMNOW`/`TONOW` both return *now − date*.
- **Button** either sets fields on the record (`now` / `today` work for dates) or opens a URL built from the record, such as `https://example.com/?q={Name}`.
- **Attachment** files are stored on the `attachments` volume, up to `MAX_UPLOAD_MB` (default 25) each. Files no record uses any more are deleted automatically within the hour (`ATTACHMENT_GC_INTERVAL`, in seconds; `0` turns this off). Images get thumbnails and can be gallery covers. Other file types are always downloaded rather than opened, so an uploaded file can never run code in Probyte.
- **User** fields can hold you, or anyone you add by their exact username. Probyte never lists other accounts.
- Computed fields are recalculated whenever records load, so they are never stale. Cross-table chains are supported; if two tables look each other up in a loop, the loop is broken.
- **Changing a field's type keeps its data.** Each value is converted from what you see: text ↔ numbers, dates, tags and checkboxes; names → linked records; usernames → users; a formula, lookup or rollup becomes its current values. Values that can't be converted are left empty. The field editor tells you what will happen before you save.
- **Large tables:** a 20,000-record table with formulas, lookups, rollups and conditional rollups loads in under a second on the server. The Table, List and Timeline views only draw the rows on screen. Board and Gallery show cards in pages ("Show more").

**Task dates:** task databases, and tasks in *tasks & events* databases, have **Started At** and **Completed At** fields:
- Moving a task to *In Progress* stamps Started At.
- *Completed* stamps Completed At.
- Moving it back clears them.
- You can still edit both by hand.

Completed At syncs with the standard iCalendar `COMPLETED` property, so phones see it. Started At is stored as `X-PROBYTE-STARTED`; if another app drops that property, Probyte keeps its own value.

## Quick start

```bash
cp .env.example .env    # then edit it
docker compose up -d --build
```

This starts four containers: `app` (web app and API), `worker` (DAV sync, webhook deliveries and cleanup, from a job queue in Postgres), `db` (Postgres) and `radicale` (the bundled CalDAV/CardDAV server). Database migrations run automatically on start.

Open http://localhost:8080 and create an account. The first account becomes the admin, and any data created before multi-user support belongs to it. Each user only sees their own databases, combined databases and CalDAV accounts.

Every new user automatically gets a **Probyte CalDAV** account, which is their login on the bundled Radicale. To sync with a phone:

1. In Probyte, click **+ (Databases)**, choose **Tasks & events** (or Tasks / Events), keep the account set to **Probyte CalDAV**, and create a new calendar.
2. Open **your name (bottom left) → Sync with your phone**. It shows the server address, username and a generated device password.
3. On the iPhone go to **Settings → Apps → Calendar → Calendar Accounts → Add Account → Other → Add CalDAV Account**, enter those three values, and turn on Calendars and Reminders.

For contacts, create a table with **New table → Contacts** on the **Probyte CalDAV** account, then on the iPhone go to **Settings → Apps → Contacts → Contacts Accounts → Add Account → Other → Add CardDAV Account** and enter the same three values. (Android: DAVx⁵ syncs calendars, tasks and contacts with one login.)

The device password is separate from the Probyte login password, and you can regenerate it at any time from the same screen.

### Reverse proxy

**One address is enough.** Probyte passes calendar requests (`PROPFIND`, `REPORT`, `/.well-known/caldav`, `/<username>/…`) through to the bundled Radicale. Point your proxy at Probyte (`app:8000`, published on `PROBYTE_PORT`) and phones can use that same address. Set `RADICALE_PUBLIC_URL` to it so the Settings screen shows the right server.

- **Turn off single sign-on for this address** (Pangolin "Platform SSO", Authelia, Authentik, Cloudflare Access…). Phones can only send a username and password, so an SSO login wall blocks them before they reach Probyte. Probyte and Radicale each do their own login.
- The proxy must pass `X-Forwarded-Proto`. Probyte then marks its session cookie `Secure` on HTTPS.
- **AI assistants and automation tools** also need to reach `/mcp`, `/oauth/…`, `/.well-known/oauth-…` and `/api/v1/…` without an SSO wall (they bring their own API key or OAuth token). Don't buffer `/mcp/sse` if you use the older SSE transport. Set `PUBLIC_URL` if the address Probyte sees isn't the public one.
- **Settings → Sync with your phone → Test connection** checks the public address the same way a phone does, and explains what's wrong if it fails.
- You can still give Radicale its own hostname (`radicale:5232`, published on `RADICALE_PORT`) if you prefer. Otherwise you can stop publishing that port.

### Configuration (env vars / `.env`)

| Variable            | Default | Meaning |
|---------------------|---------|---------|
| `PROBYTE_PORT`      | `8080`  | Host port for the web UI |
| `RADICALE_PORT`     | `5232`  | Host port for the bundled CalDAV server |
| `RADICALE_PUBLIC_URL` | –     | Public URL of the bundled CalDAV server, shown to users for phone setup |
| `ALLOW_SIGNUP`      | `true`  | Let anyone who can reach the site create an account. The first account can always be created. |
| `POSTGRES_PASSWORD` | `probyte` | Database password |
| `DEFAULT_TZ`        | `UTC`   | Timezone used for floating CalDAV times and all-day dates. Set this to your own timezone, e.g. `Europe/Berlin`. |
| `SYNC_INTERVAL`     | `300`   | Seconds between background syncs of every CalDAV database (`0` = off) |
| `AUTO_PUSH_DELAY`   | `2`     | Seconds after a local edit before it is pushed to the server (`-1` = only push on sync) |
| `WORKER_THREADS`    | `4`     | Jobs the worker runs at once |
| `RUN_WORKER`        | –       | `embedded` runs the worker inside the web app (single-container setups without the `worker` service) |
| `ATTACHMENT_GC_INTERVAL` | `3600` | Seconds between cleanups of uploaded files no record uses any more |
| `COMPUTE_CACHE_ROWS` | `300000` | Records kept computed in memory between requests (`0` = off) |
| `SESSION_DAYS`      | `30`    | How long a login lasts |
| `REMOTE_ALLOW_PRIVATE` | `false` | Allow linked tables from Probyte servers on private/LAN addresses |
| `WEBHOOK_ALLOW_PRIVATE` | `false` | Allow webhooks to private/LAN addresses (e.g. n8n or Home Assistant at home) |
| `MCP_BEARER_TOKEN`  | –       | A fixed bearer token (16+ characters) for MCP clients |
| `MCP_BEARER_USER`   | first admin | Whose data the fixed token uses |
| `MCP_BEARER_SCOPE`  | `write` | `read` limits the fixed token to the read tools |
| `PUBLIC_URL`        | –       | Public address of Probyte (e.g. `https://probyte.example.com`), used for OAuth/MCP discovery and the OpenAPI spec; defaults to what the proxy forwards |
| `COOKIE_SECURE`     | `auto`  | `auto` = Secure cookie when the request came in over HTTPS; `true` / `false` to force it |

## How sync works

- **Incremental:** syncs run in the worker. When the calendar's change tag (CTag / sync-token) is unchanged, nothing is downloaded; otherwise only the items whose ETag changed are fetched (multiget). A table syncs every `SYNC_INTERVAL`, a couple of seconds after a local edit, and on **Sync**.
- **Pull:** every remote object is compared with the copy stored at the last sync. New objects are imported, changed ones update the record, and records whose object was deleted on the server are removed.
- **Push:** records edited in Probyte (marked with an orange dot in the grid) are written back. Unknown iCalendar properties such as alarms, recurrence rules and attendees are kept, because Probyte only rewrites the properties it manages.
- **Conflicts:** if a record was edited locally since the last sync, the local edit wins. Otherwise the server copy wins.
- Deleting a record also deletes it on the server. Deleting a *table* also deletes its data on the server: on Probyte's own server the whole calendar / address book is removed (phones lose it too); for other accounts you can tick "also delete its items" (the calendar itself stays). If another table uses the same calendar, the server data is kept.
- You can add your own extra fields to a synced table. They are stored only in Probyte. The synced fields (Title, Status, Due, …, marked with a lock in the grid) are **locked**: they can't be renamed, changed or deleted, only hidden in a view.
- Tasks have a **Completed** checkbox that decides whether the task is done on the server: ticking it sets Status to Completed (and Completed At), unticking sets it back to To Do, and changing Status updates the box. Events have the same column, ticked automatically once the event has ended.
- **Completed At** is when the task was completed. When another device completes a task, Probyte records the time of that change (some apps, like iOS Reminders, write the reminder's scheduled time into `COMPLETED`).

| Tasks (VTODO)                 | Events (VEVENT)                |
|-------------------------------|--------------------------------|
| Title → `SUMMARY`             | Title → `SUMMARY`              |
| Status → `STATUS` (+`COMPLETED`) | Start / End → `DTSTART` / `DTEND` |
| Priority → `PRIORITY` (1/5/9) | All Day → date-valued start/end |
| Due → `DUE`, Start → `DTSTART`| Location → `LOCATION`          |
| Description, Tags → `DESCRIPTION`, `CATEGORIES` | Status, Description, Tags |
| **Completed** (checkbox) → `STATUS:COMPLETED` | Completed (checkbox): ticked once the event is over (not stored on the server) |
| Started At → `X-PROBYTE-STARTED`, Completed At → `COMPLETED`, Location → `LOCATION` | Priority → `PRIORITY` (tasks & events tables) |
| Repeat → `RRULE` + `EXDATE`   | Repeat → `RRULE` + `EXDATE`    |
| | Status **Attended** / **Missed** → `STATUS:CONFIRMED` + `X-PROBYTE-STATUS` (iCalendar has no such statuses) |

Repeat rules set in other apps are kept exactly, even ones the Repeat editor can't show (it then offers to replace them). Skipping a date adds an `EXDATE`. Changing a single occurrence writes an iCalendar exception (a `RECURRENCE-ID` component with its own start, end and title, copying the series' location and notes); "Reset to series" removes it, and exceptions made on other devices, including cancelled ones, are read the same way. To move a whole series, change its start in the panel. Probyte doesn't use subtasks: a `RELATED-TO` set by another app is left as it is.

| Contacts (vCard)              |                                |
|-------------------------------|--------------------------------|
| Name → `FN`, First / Last Name → `N` | Company → `ORG`, Job Title → `TITLE` |
| Email → first `EMAIL`, Phone → first `TEL` | Address → first `ADR` |
| Birthday → `BDAY`, Website → `URL` | Notes → `NOTE`, Groups → `CATEGORIES` |

For contacts, Probyte manages the preferred (or first) email, phone and address. Additional numbers and emails, photos, Apple labels and any other vCard properties are kept as they are, and a property is only rewritten when its value changed in Probyte. An address edited in Probyte is written as one line (vCard addresses have separate street/city/postcode parts that free text can't be split back into).

In a **tasks & events** database, *Type* chooses the component. *End / Due* is written as `DUE` for tasks and as `DTEND` for events. Changing a record's Type turns the server object from a VTODO into a VEVENT (or back). A record created in the calendar view starts as an Event; anywhere else it starts as a Task.

**iCloud:** iCloud calendars work over CalDAV with an app-specific password (`https://caldav.icloud.com/`), but only for events. Since iOS 13, iCloud Reminders no longer use CalDAV. To get tasks onto an iPhone, use the built-in Probyte CalDAV account, or another CalDAV server such as Nextcloud or Fastmail.

## How it stays fast

- **Computed values are cached.** Formulas, lookups and rollups are calculated when a table is read, then kept in memory against a per-table version that database triggers move on every change. When only some records changed, just those are read and recalculated. Writing a record calculates only that record.
- **Live updates instead of reloading.** The database announces every change (`NOTIFY`); the web app relays it to open pages over `/api/events`, and pages fetch only the changed records (`/api/tables/{id}/changes`). Changes from phones, the API, assistants and other tabs appear within a second or two. Tables you've opened are kept in memory, so switching back is instant.
- **Small downloads.** API responses are gzipped (a 50,000-row table is about 3 MB), and each view and dialog loads only when first used.
- **Background work is queued.** Sync, webhooks and cleanup are jobs in Postgres run by the worker: they survive restarts, retry with backoff, and never slow down a request.

Rough timings on a small server (`scripts/test.sh bench`):

| Rows | Open a table | Open right after an edit | Edit a record |
|------|--------------|--------------------------|---------------|
| 10,000 | 0.09 s | 0.09 s | 0.02 s |
| 50,000 | 0.4 s | 0.5 s | 0.04 s |

## Development

```bash
docker compose up -d db                        # postgres on :5432

cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000      # API on :8000 (OpenAPI docs at /docs)
python -m app.worker                           # job worker (sync, webhooks); or RUN_WORKER=embedded above

cd ../frontend
npm install
npm run dev                                    # UI on :5173, proxies /api to :8000
```

### Tests

```bash
scripts/test.sh            # everything: API tests (pytest) and browser tests (Playwright)
scripts/test.sh api        # just the API tests (or: ui);  scripts/test.sh -k sync   any pytest arguments
scripts/test.sh tests/api/test_fields.py   # one file or test
scripts/test.sh bench      # timings at 1k / 10k / 50k rows
KEEP_STACK=1 scripts/test.sh ...   # leave the test stack running afterwards to inspect it
```

Tests run against a separate copy of the stack (Compose project `probyte-test`, ports 18080/15232, its own database and Radicale), never your real data. Only Docker is needed. Failed browser tests leave a screenshot in `tests/.artifacts/`.

What's covered: every field type, formulas, links, lookups and rollups, type changes; records, views, filters, imports, bulk edits; users kept apart; the REST API, every MCP tool, OAuth and webhooks (filters, retries); public links and linked tables; two-way calendar and contacts sync (phones' items, conflicts, deletions, repeats, time zones); live updates and the computed-value cache; and the app itself in a browser on a desktop and a phone (building tables, editing, filtering, board, calendar, import/export, settings).

### Database changes

Add a numbered file to `backend/app/migrations/`: `NNNN_name.sql`, or `NNNN_name.py` with a `run(conn)` function. Each runs once, in order, when the app or worker starts.

### Layout

```
backend/app/
  main.py         the FastAPI app: middleware, routers, the built frontend, the pass-through to Radicale
  routes/         the web app's JSON API: auth, workspace (bases, accounts), tables & fields, records, views & combos
  live.py         live updates: /api/events (Server-Sent Events) fed by Postgres LISTEN/NOTIFY
  worker.py       the worker process (python -m app.worker): job threads + periodic schedule
  jobs.py         durable job queue in Postgres (debounced, retried, SKIP LOCKED)
  values.py       turning input into stored values: API writes, imports, field type changes
  auth.py         accounts (scrypt password hashes) and cookie sessions
  builtin_dav.py  per-user logins on the bundled Radicale (writes its htpasswd file)
  store.py        tables / fields / records / links / combined-database data access; every record write
                  ends in store._changed (webhook event + DAV push queued in the same transaction)
  compute.py      computed fields (formula, lookup, rollup, conditional, system fields), the shared cache,
                  and change tokens for /changes
  formula.py      Teable-style formula language: parser, functions, type inference
  attachments.py  file uploads for attachment fields
  remote.py       linked tables: reading/writing another Probyte's public link (local fast path or HTTP)
  recordops.py    record writes for every table kind (local, CalDAV, linked)
  shares.py       public links (owner management, visitor API, PWA manifest/icons)
  sync.py      CalDAV/CardDAV two-way sync (incremental: CTag/sync-token + ETags), run as worker jobs
  dav.py       WebDAV plumbing shared by CalDAV and CardDAV (multistatus, collection state, multiget)
  apiv1.py     public API core (tables by name, value conversion, queries, batch writes, upsert)
  rest_v1.py   REST API v1 at /api/v1 (+ OpenAPI and docs)
  mcp_server.py  MCP server at /mcp (Streamable HTTP + SSE)
  oauth.py     OAuth 2.1 for MCP clients (discovery, registration, consent page, tokens)
  webhooks.py  record events → signed POSTs to user URLs
  ical.py      record <-> VTODO/VEVENT conversion, CalDAV system fields
  recur.py     repeat rules: validation, next occurrence for repeating tasks
  carddav.py   CardDAV address book discovery and creation
  vcard.py     record <-> vCard conversion that keeps unmanaged properties
  migrate.py   runs migrations/ (numbered .sql / .py files, each once; recorded in schema_migrations)
  migrations/  0001 baseline schema, data fixes, jobs + sync state, table/row versions, live changes
radicale/      bundled Radicale image: htpasswd auth, owner-only rights
frontend/src/
  App.tsx                    shell + hash routing
  data.ts                    in-memory dataset cache, incremental refresh (/changes), live event stream
  components/DatasetPage.tsx one database / combined database: views, toolbar, record ops
  components/*View.tsx       grid, kanban, calendar, timeline, list, gallery, chart (each loaded on first use)
tests/         api/ (pytest), ui/ (Playwright), bench/ (timings); run with scripts/test.sh
```

Records are stored as JSONB keyed by field id. A combined database stores only its source list and a field mapping, so there is no duplicated data.

## Notes and limitations

- Signed-in users can't share bases with each other yet; use public links to let others in. There is no admin user-management screen; set `ALLOW_SIGNUP=false` once everyone has signed up.
- Dragging calendar items is a mouse/trackpad feature. On phones, tap a day to list its items, and open a record to change its date. Board cards have a **Move to** button on touch screens. Timeline bars can be dragged with touch.
- There is no rate limiting on Probyte's login. Radicale delays failed logins, and your reverse proxy can rate-limit `/api/auth/login`.
- CalDAV passwords for external accounts, and the generated device passwords, are stored in plain text in Postgres, because Probyte needs them to connect. Probyte login passwords are hashed.
- Don't change `RADICALE_URL` (the internal address) after creating databases on the built-in account. Their calendar URLs are stored with it.
- Recurring events are shown once, on their first occurrence. Their recurrence rules are kept on the server.
