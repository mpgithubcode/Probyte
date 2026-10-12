import { useEffect, useState } from "react";
import { api } from "../api";
import { loadExtensions, type ExtensionInfo } from "../extensions";
import { Icon } from "./Icon";

/** Settings → Extensions (the server's admin): optional parts of Probyte, on or off for everyone. */
export function ExtensionsSettings() {
  const [list, setList] = useState<ExtensionInfo[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { loadExtensions(true).then(setList); }, []);
  const toggle = async (e: ExtensionInfo) => {
    setErr("");
    try {
      setList(await api.put<ExtensionInfo[]>(`/extensions/${e.id}`, { enabled: !e.enabled }));
      loadExtensions(true);  // the sidebar
    } catch (x) { setErr((x as Error).message); }
  };
  if (!list) return <p className="muted">Loading…</p>;
  return (
    <div className="form">
      <p className="muted small">Optional parts of Probyte. Switched on, an extension shows in everyone's sidebar under <b>Extensions</b>;
        switched off, it's hidden and its data is kept.</p>
      {!list.length && <p className="muted">No extensions are installed.</p>}
      {list.map((e) => (
        <div key={e.id} className="list-card ext-card">
          <div className="list-card-head">
            <span className="ext-icon"><Icon name={e.icon} size={18} /></span>
            <div className="list-card-title"><b>{e.name}</b><span className="muted small">{e.description}</span></div>
            <span className="spacer" />
            <label className="switch" title={e.enabled ? "On" : "Off"}>
              <input type="checkbox" checked={e.enabled} onChange={() => toggle(e)} aria-label={`${e.name} on`} />
              <span />
            </label>
          </div>
        </div>
      ))}
      {err && <div className="error">{err}</div>}
    </div>
  );
}
