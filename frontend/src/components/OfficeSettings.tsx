import { useEffect, useState } from "react";
import { api } from "../api";
import { resetOfficeStatus } from "./OfficeEditor";

interface Cfg { url: string; probyte_url: string; server_url: string; via_probyte: boolean; has_secret: boolean }

/** Settings → Document editing (the server's admin): the ONLYOFFICE Document Server that edits files of files tables. */
export function OfficeSettings() {
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [secret, setSecret] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<Cfg>("/office/settings").then((c) => { setCfg(c); setAdvanced(!!(c.probyte_url || c.server_url)); }).catch((e) => setErr(e.message)); }, []);
  if (!cfg) return <p className="muted">{err || "Loading…"}</p>;
  const set = (p: Partial<Cfg>) => setCfg({ ...cfg, ...p });

  const save = async (test: boolean) => {
    setBusy(true); setErr(""); setMsg("");
    try {
      const saved = await api.put<Cfg>("/office/settings", { url: cfg.url, probyte_url: cfg.probyte_url, server_url: cfg.server_url,
        via_probyte: cfg.via_probyte, secret: secret || undefined });
      setCfg(saved); setSecret(""); resetOfficeStatus();
      if (test && saved.url) {
        const r = await api.post<{ version?: string }>("/office/settings/test");
        setMsg(`Connected both ways${r.version ? ` to ONLYOFFICE Docs ${r.version}` : ""}. Files can now be edited from the file viewer (Edit ▾).`);
      } else setMsg(saved.url ? "Saved." : "Document editing is off.");
    } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="form">
      <p className="muted small">Edit Word, Excel, PowerPoint and OpenDocument files of files tables in an <b>ONLYOFFICE Docs</b> (Document Server)
        right in the file viewer: <b>Edit ▾ → Edit in ONLYOFFICE</b>. Saved versions are written to the file's S3 or WebDAV storage.</p>
      <label className="stack">Document server address
        <input className="input" value={cfg.url} placeholder={cfg.via_probyte ? "http://onlyoffice or http://192.168.1.10:8081" : "https://office.example.com"}
          onChange={(e) => set({ url: e.target.value })} />
        <span className="hint">{cfg.via_probyte
          ? "Where Probyte reaches it: its Docker name or local network address is fine."
          : "The address browsers open it at: every browser must reach it, over HTTPS when Probyte uses HTTPS."} Leave empty to turn document editing off.</span>
      </label>
      <div className="stack">How browsers open the editor
        <label className="inline-check"><input type="radio" name="office-via" checked={cfg.via_probyte} onChange={() => set({ via_probyte: true })} />
          Through Probyte (recommended): works anywhere Probyte does, remotely and over HTTPS</label>
        <label className="inline-check"><input type="radio" name="office-via" checked={!cfg.via_probyte} onChange={() => set({ via_probyte: false })} />
          Directly from the document server's own public address</label>
      </div>
      <label className="stack">Secret (JWT)
        <input className="input" type="password" value={secret} autoComplete="new-password" placeholder={cfg.has_secret ? "(unchanged)" : "The Document Server's JWT_SECRET"}
          onChange={(e) => setSecret(e.target.value)} />
        <span className="hint">The same secret as the Document Server's <code>JWT_SECRET</code>: every exchange is signed with it. Stored encrypted.</span>
      </label>
      <button type="button" className="link-btn" onClick={() => setAdvanced(!advanced)}>{advanced ? "Hide" : "Show"} network settings</button>
      {advanced && (<>
        <label className="stack">Probyte's address for the document server
          <input className="input" value={cfg.probyte_url} placeholder="(Probyte's public address)" onChange={(e) => set({ probyte_url: e.target.value })} />
          <span className="hint">Where the document server fetches files and reports saves, e.g. <code>http://probyte:8000</code> when both run in the same Docker network.</span>
        </label>
        {!cfg.via_probyte && <label className="stack">The document server's address for Probyte
          <input className="input" value={cfg.server_url} placeholder="(the address above)" onChange={(e) => set({ server_url: e.target.value })} />
          <span className="hint">Where Probyte sends commands (Save) and gets saved files, e.g. <code>http://onlyoffice</code> in the same Docker network.</span>
        </label>}
      </>)}
      <div className="settings-actions">
        <button className="btn primary" disabled={busy} onClick={() => save(true)}>{busy ? "Checking…" : "Save and test"}</button>
        <button className="btn" disabled={busy} onClick={() => save(false)}>Save</button>
      </div>
      {msg && <div className="ok small">{msg}</div>}
      {err && <div className="error">{err}</div>}
      <details className="hint">
        <summary>Running a document server</summary>
        <p>Add it to Probyte's docker-compose.yml (it needs about 2 GB of memory):</p>
        <pre className="code-block">{`  onlyoffice:
    image: onlyoffice/documentserver:8.2.2
    restart: unless-stopped
    environment:
      JWT_SECRET: a-long-random-secret
      ALLOW_PRIVATE_IP_ADDRESS: "true"   # lets it fetch files from Probyte over the Docker network
    ports:
      - "8081:80"   # or behind your reverse proxy`}</pre>
        <p>Then enter its public address and the secret here; under network settings, <code>http://probyte:8000</code> and <code>http://onlyoffice</code>.</p>
      </details>
    </div>
  );
}
