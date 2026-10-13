import { useEffect, useState } from "react";
import { api } from "../api";
import { loadExtensions, type ExtensionInfo } from "../extensions";
import { tk } from "../extensions/takeoff/tk";
import { Icon } from "./Icon";

/** Settings → Extensions: optional parts of Probyte, switched on or off for everyone by the server's admin, and
 * each one's own settings. */
export function ExtensionsSettings({ admin }: { admin: boolean }) {
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
      <p className="muted small">Optional parts of Probyte. Switched on{admin ? "" : " by the server's admin"}, an extension shows in everyone's
        sidebar under <b>Extensions</b>; switched off, it's hidden and its data is kept.</p>
      {!list.length && <p className="muted">No extensions are installed.</p>}
      {list.filter((e) => admin || e.enabled).map((e) => (
        <div key={e.id} className="list-card ext-card">
          <div className="list-card-head">
            <span className="ext-icon"><Icon name={e.icon} size={18} /></span>
            <div className="list-card-title"><b>{e.name}</b><span className="muted small">{e.description}</span></div>
            <span className="spacer" />
            {admin && (
              <label className="switch" title={e.enabled ? "On" : "Off"}>
                <input type="checkbox" checked={e.enabled} onChange={() => toggle(e)} aria-label={`${e.name} on`} />
                <span />
              </label>
            )}
          </div>
          {e.id === "takeoff" && e.enabled && <TakeoffSettings />}
        </div>
      ))}
      {err && <div className="error">{err}</div>}
    </div>
  );
}

/** Which fields of the drawing set's file the files a takeoff saves next to it copy (its folder, tags…). */
function TakeoffSettings() {
  const [st, setSt] = useState<{ inherit: string[] | null; fields: string[] } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { tk.settings().then(setSt).catch((x) => setErr(x.message)); }, []);
  if (!st) return err ? <div className="error">{err}</div> : null;
  const save = (inherit: string[] | null) => {
    setErr("");
    setSt({ ...st, inherit });
    tk.saveSettings(inherit).then(setSt).catch((x) => { setErr(x.message); tk.settings().then(setSt); });
  };
  const chosen = new Set(st.inherit ?? st.fields);
  return (
    <div className="ext-settings">
      <b className="small">Files saved next to the drawings copy from the drawing set's file</b>
      <label className="inline-check"><input type="radio" checked={st.inherit === null} onChange={() => save(null)} /> Its folder and every field you added</label>
      <label className="inline-check"><input type="radio" checked={st.inherit !== null} onChange={() => save(st.fields)} /> Only these fields:</label>
      {st.inherit !== null && (
        <div className="ext-fields">
          {!st.fields.length && <span className="muted small">Your files tables have no fields to copy yet.</span>}
          {st.fields.map((n) => (
            <label key={n} className="inline-check">
              <input type="checkbox" checked={chosen.has(n)} onChange={() => save(chosen.has(n) ? [...chosen].filter((x) => x !== n) : [...chosen, n])} /> {n}
            </label>
          ))}
        </div>
      )}
      {err && <div className="error">{err}</div>}
    </div>
  );
}
