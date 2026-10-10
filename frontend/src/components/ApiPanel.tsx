import { useEffect, useState } from "react";
import { api } from "../api";
import type { Workspace } from "../types";
import { timeAgo } from "../util";
import { ApiKeysPanel } from "./ApiKeysPanel";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

interface Webhook {
  id: string; name: string; url: string; events: string[]; tables: string[] | null; enabled: boolean;
  last_status: number | null; last_error: string | null; last_delivery_at: string | null; secret?: string;
}
const EVENTS = [
  { id: "record.created", label: "Record created" },
  { id: "record.updated", label: "Record updated" },
  { id: "record.deleted", label: "Record deleted" },
];

function Copy({ value, label }: { value: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="copy-field">
      {label && <span className="copy-label">{label}</span>}
      <code>{value}</code>
      <button className="icon-btn" title="Copy" onClick={async () => {
        try { await navigator.clipboard.writeText(value); setDone(true); setTimeout(() => setDone(false), 1200); } catch { /* select manually */ }
      }}>{done ? "✓" : <Icon name="copy" size={14} />}</button>
    </div>
  );
}

function Snippet({ text }: { text: string }) {
  return <div className="snippet"><pre className="code-block">{text}</pre><Copy value={text} /></div>;
}

/** Settings → API & MCP: connecting AI assistants, the REST API, keys and webhooks. */
export function ApiPanel({ ws }: { ws: Workspace }) {
  const origin = location.origin;
  const mcp = `${origin}/mcp`;
  return (
    <div className="api-panel">
      <section className="settings-section">
        <h3><Icon name="sparkle" size={16} /> Connect an AI assistant (MCP)</h3>
        <p className="muted">
          Probyte is an MCP server: assistants can list, search, create and update your records, and create tables.
          Apps that support sign-in connect with just this address; you approve them in Probyte and choose read-only or read &amp; write.
        </p>
        <Copy label="MCP URL" value={mcp} />
        <BearerToken mcp={mcp} />
        <details className="howto">
          <summary>Claude (claude.ai, desktop and mobile apps)</summary>
          <ol>
            <li>Settings → Connectors → <b>Add custom connector</b>.</li>
            <li>Name it Probyte, paste the MCP URL, and click Add → <b>Connect</b>.</li>
            <li>Sign in to Probyte in the window that opens and click <b>Allow</b>.</li>
          </ol>
        </details>
        <details className="howto">
          <summary>ChatGPT</summary>
          <ol>
            <li>Settings → Apps &amp; Connectors → Advanced settings → turn on <b>Developer mode</b>.</li>
            <li>Create a connector: paste the MCP URL, Authentication <b>OAuth</b>.</li>
            <li>Sign in to Probyte and click <b>Allow</b>. Then pick Probyte in a chat's developer-mode tools.</li>
          </ol>
        </details>
        <details className="howto">
          <summary>Claude Code</summary>
          <Snippet text={`claude mcp add --transport http probyte ${mcp}\n# then run /mcp in Claude Code and choose "Authenticate"`} />
          <p className="hint">Or with an API key: add <code>--header "Authorization: Bearer pbk_…"</code>.</p>
        </details>
        <details className="howto">
          <summary>Cursor, VS Code, Windsurf, LibreChat and other MCP clients</summary>
          <p className="hint">Use the MCP URL (most sign in by themselves), or add an API key header:</p>
          <Snippet text={JSON.stringify({ mcpServers: { probyte: { url: mcp, headers: { Authorization: "Bearer pbk_…" } } } }, null, 2)} />
          <p className="hint">VS Code uses <code>"servers"</code> with <code>"type": "http"</code> in <code>.vscode/mcp.json</code>.</p>
        </details>
        <details className="howto">
          <summary>n8n (AI Agent → MCP Client Tool)</summary>
          <ol>
            <li>Endpoint: <code>{mcp}</code>, Server transport: <b>HTTP Streamable</b>.</li>
            <li>Authentication: <b>Bearer</b>, with an API key from below.</li>
            <li>Older n8n versions: transport <b>Server Sent Events</b> with endpoint <code>{origin}/mcp/sse</code>.</li>
          </ol>
        </details>
        <details className="howto">
          <summary>Apps that only run local (stdio) servers</summary>
          <Snippet text={`npx -y mcp-remote ${mcp} --header "Authorization: Bearer pbk_…"`} />
        </details>
        <details className="howto">
          <summary>Apps that only accept a URL</summary>
          <p className="hint">Put a key in the address: <code>{mcp}?key=pbk_…</code>. Proxies may log full URLs, so use a dedicated key
            (read-only if you can) that you can revoke.</p>
        </details>
      </section>

      <section className="settings-section">
        <h3><Icon name="code" size={16} /> REST API</h3>
        <p className="muted">
          For scripts and automation tools (n8n, Make, Zapier, Home Assistant, GPT Actions). Tables and fields go by name, values as plain text,
          with paging, filters, batch writes and upserts.
        </p>
        <div className="row wrap">
          <a className="btn sm" href="/api/v1/docs" target="_blank" rel="noreferrer"><Icon name="external" size={14} /> Interactive docs</a>
          <a className="btn sm" href="/api/v1/openapi.json" target="_blank" rel="noreferrer"><Icon name="download" size={14} /> OpenAPI spec</a>
        </div>
        <details className="howto">
          <summary>Examples</summary>
          <Snippet text={`KEY=pbk_…   # an API key from below

# bases and tables
curl -H "Authorization: Bearer $KEY" ${origin}/api/v1/bases

# records: filter, sort, page (tables by id or name)
curl -G -H "Authorization: Bearer $KEY" "${origin}/api/v1/tables/Tasks/records" \\
  --data-urlencode 'filter={"Status": "To Do"}' --data-urlencode 'sort=-Due' -d limit=50

# create (dates, select options, linked titles as plain text)
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"fields": {"Title": "Call the plumber", "Due": "2026-10-20"}}' \\
  ${origin}/api/v1/tables/Tasks/records

# create or update, matched on a field
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"match_on": ["Email"], "records": [{"fields": {"Email": "ada@example.com", "Stage": "Won"}}]}' \\
  ${origin}/api/v1/tables/Leads/records/upsert`} />
        </details>
      </section>

      <section className="settings-section">
        <h3><Icon name="key" size={16} /> API keys &amp; connected apps</h3>
        <ApiKeysPanel ws={ws} />
      </section>

      <section className="settings-section">
        <h3><Icon name="bolt" size={16} /> Webhooks</h3>
        <WebhooksPanel ws={ws} />
      </section>
    </div>
  );
}

/** A dedicated bearer token for MCP clients configured with "Bearer token" / header auth. */
function BearerToken({ mcp }: { mcp: string }) {
  const [scope, setScope] = useState<"write" | "read">("write");
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState("");
  const create = async () => {
    setError("");
    try {
      const k = await api.post<{ key: string }>("/keys", { name: `MCP token (${new Date().toLocaleDateString()})`, scope, base_ids: null });
      setToken(k.key);
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <div className="bearer-box">
      <div className="row wrap">
        <b className="small">Bearer token</b>
        <span className="muted small" style={{ flex: 1, minWidth: 180 }}>For clients set up with a token instead of sign-in.</span>
        <div className="segmented sm">
          <button className={scope === "write" ? "active" : ""} onClick={() => setScope("write")}>Read &amp; write</button>
          <button className={scope === "read" ? "active" : ""} onClick={() => setScope("read")}>Read only</button>
        </div>
        <button className="btn sm primary" onClick={create}><Icon name="key" size={14} /> Create MCP token</button>
      </div>
      {token && (
        <>
          <div className="notice small">Copy it now — it won't be shown again. It's listed under API keys below, where you can revoke it.</div>
          <Copy label="Token" value={token} />
          <Snippet text={`URL:     ${mcp}
Header:  Authorization: Bearer ${token}`} />
        </>
      )}
      {error && <div className="error">{error}</div>}
      <p className="hint">Send it as <code>Authorization: Bearer &lt;token&gt;</code>. Server admins can also set a fixed token with
        <code> MCP_BEARER_TOKEN</code> (and <code>MCP_BEARER_USER</code>) in Probyte's environment.</p>
    </div>
  );
}

function WebhooksPanel({ ws }: { ws: Workspace }) {
  const [hooks, setHooks] = useState<Webhook[] | null>(null);
  const [editing, setEditing] = useState<Webhook | "new" | null>(null);
  const [test, setTest] = useState<Record<string, string>>({});
  const load = () => api.get<{ webhooks: Webhook[] }>("/v1/webhooks").then((r) => setHooks(r.webhooks)).catch(() => setHooks([]));
  useEffect(() => { load(); }, []);
  const tableName = (id: string) => ws.tables.find((t) => t.id === id)?.name ?? "?";

  const runTest = async (h: Webhook) => {
    setTest((t) => ({ ...t, [h.id]: "Sending…" }));
    try {
      const r = await api.post<{ ok: boolean; status: number | null; error: string | null }>(`/v1/webhooks/${h.id}/test`);
      setTest((t) => ({ ...t, [h.id]: r.ok ? `Delivered (HTTP ${r.status})` : `Failed: ${r.error ?? `HTTP ${r.status}`}` }));
    } catch (e) { setTest((t) => ({ ...t, [h.id]: (e as Error).message })); }
    load();
  };

  return (
    <>
      <div className="row">
        <p className="muted" style={{ flex: 1 }}>Probyte POSTs a JSON event to your URL when records are created, changed or deleted —
          from the app, the API, an assistant or a phone sync. Each request is signed with the webhook's secret.</p>
        <button className="btn primary sm" onClick={() => setEditing("new")}><Icon name="plus" size={14} /> New webhook</button>
      </div>
      {hooks?.length === 0 && <div className="empty-card">No webhooks yet.</div>}
      {hooks?.map((h) => (
        <div key={h.id} className={`list-card ${h.enabled ? "" : "off"}`}>
          <div className="list-card-head">
            <Icon name="bolt" size={18} />
            <div className="list-card-title">
              <b>{h.name}</b>
              <span className="muted small"><code>{h.url}</code></span>
              <span className="muted small">
                {h.tables === null ? "All tables" : h.tables.map(tableName).join(", ")} · {h.events.map((e) => e.replace("record.", "")).join(", ")}
                {h.last_delivery_at && <> · last sent {timeAgo(h.last_delivery_at)} {h.last_error ? <span className="err-text">({h.last_error})</span> : "✓"}</>}
              </span>
              {test[h.id] && <span className="small">{test[h.id]}</span>}
            </div>
            <span className="spacer" />
            {!h.enabled && <span className="badge">paused</span>}
            <button className="btn sm" onClick={() => runTest(h)}>Test</button>
            <button className="icon-btn" title="Edit" onClick={() => setEditing(h)}><Icon name="edit" size={14} /></button>
          </div>
        </div>
      ))}
      <details className="howto">
        <summary>Event format and signature check</summary>
        <Snippet text={`POST <your URL>
X-Probyte-Event: record.updated
X-Probyte-Signature: sha256=<HMAC-SHA256 of the raw body, keyed with the secret>

{"id": "<delivery id>", "event": "record.updated", "created_at": "…",
 "base": {"id": "…", "name": "My Base"}, "table": {"id": "…", "name": "Tasks"},
 "record": {"id": "…", "fields": {"Title": "Call the plumber", "Status": "Done"}}}`} />
        <p className="hint">Changes to one record within a second are sent as one event. Failed deliveries are retried three times.
          Sending to addresses on your own network (e.g. n8n at 192.168.x.x) needs <code>WEBHOOK_ALLOW_PRIVATE=true</code> on the server.</p>
      </details>
      {editing && <WebhookEditor ws={ws} hook={editing === "new" ? null : editing} onClose={() => setEditing(null)}
        onSaved={() => { load(); }} onDeleted={() => { setEditing(null); load(); }} />}
    </>
  );
}

function WebhookEditor({ ws, hook, onClose, onSaved, onDeleted }: {
  ws: Workspace; hook: Webhook | null; onClose: () => void; onSaved: () => void; onDeleted: () => void;
}) {
  const [name, setName] = useState(hook?.name ?? "");
  const [url, setUrl] = useState(hook?.url ?? "");
  const [events, setEvents] = useState<string[]>(hook?.events ?? EVENTS.map((e) => e.id));
  const [all, setAll] = useState(hook ? hook.tables === null : true);
  const [tables, setTables] = useState<string[]>(hook?.tables ?? []);
  const [enabled, setEnabled] = useState(hook?.enabled ?? true);
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const localTables = ws.tables.filter((t) => t.kind !== "remote");

  const save = async () => {
    if (!url.trim()) return setError("Enter the URL to send events to");
    if (!events.length) return setError("Choose at least one event");
    if (!all && !tables.length) return setError("Choose at least one table, or all tables");
    setError("");
    const body = { name: name.trim() || "Webhook", url: url.trim(), events, enabled, ...(all ? { all_tables: true, tables: null } : { tables }) };
    try {
      const h = hook ? await api.patch<Webhook>(`/v1/webhooks/${hook.id}`, body)
        : await api.post<Webhook>("/v1/webhooks", { ...body, tables: all ? null : tables });
      onSaved();
      if (hook) onClose(); else setSecret(h.secret ?? null);
    } catch (e) { setError((e as Error).message); }
  };

  if (secret) {
    return (
      <Modal title="Webhook created" width={520} onClose={onClose}
        footer={<><span className="spacer" /><button className="btn primary" onClick={onClose}>Done</button></>}>
        <div className="form">
          <p className="muted">Use this secret to check that requests really come from Probyte (see “Event format and signature check”).
            It's also shown when you edit the webhook.</p>
          <Copy label="Secret" value={secret} />
        </div>
      </Modal>
    );
  }

  return (
    <Modal title={hook ? "Edit webhook" : "New webhook"} width={500} onClose={onClose}
      footer={<>
        {hook && (confirm
          ? <><button className="btn danger" onClick={async () => { await api.del(`/v1/webhooks/${hook.id}`); onDeleted(); }}>Delete webhook</button>
              <button className="btn" onClick={() => setConfirm(false)}>Cancel</button></>
          : <button className="btn danger-ghost" onClick={() => setConfirm(true)}><Icon name="trash" size={14} /> Delete</button>)}
        <span className="spacer" />
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={save}>{hook ? "Save" : "Create webhook"}</button>
      </>}>
      <div className="form">
        <label className="stack">Name<input className="input" autoFocus value={name} placeholder="n8n: new tasks" onChange={(e) => setName(e.target.value)} /></label>
        <label className="stack">URL<input className="input" value={url} placeholder="https://n8n.example.com/webhook/…" onChange={(e) => setUrl(e.target.value)} /></label>
        <div className="stack">Events
          {EVENTS.map((ev) => (
            <label key={ev.id} className="inline-check">
              <input type="checkbox" checked={events.includes(ev.id)} onChange={(e) => setEvents(e.target.checked ? [...events, ev.id] : events.filter((x) => x !== ev.id))} />
              {ev.label}
            </label>
          ))}
        </div>
        <div className="stack">Tables
          <label className="inline-check"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> All tables (including ones created later)</label>
          {!all && localTables.map((t) => (
            <label key={t.id} className="inline-check">
              <input type="checkbox" checked={tables.includes(t.id)} onChange={(e) => setTables(e.target.checked ? [...tables, t.id] : tables.filter((x) => x !== t.id))} />
              {t.name} <span className="muted small">{ws.bases.find((b) => b.id === t.base_id)?.name}</span>
            </label>
          ))}
        </div>
        {hook && <label className="inline-check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Active</label>}
        {hook?.secret && <Copy label="Secret" value={hook.secret} />}
        {error && <div className="error">{error}</div>}
      </div>
    </Modal>
  );
}
