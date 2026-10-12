import { useRef, useState } from "react";
import { api } from "../api";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

interface Result { created: number; updated: number; skipped: number; errors: string[]; error_count: number }

/** Contacts tables: add contacts from vCard files (.vcf) exported from a phone, Google, Outlook or iCloud. */
export function ContactsImport({ url, canUpdate = true, initialFiles, onClose, onDone }: {
  url: string;            // the table's (or a linked table's) vCard import address
  canUpdate?: boolean;    // a linked table whose link doesn't allow editing can only add
  initialFiles?: File[]; onClose: () => void; onDone: () => void;
}) {
  const [files, setFiles] = useState<File[]>(initialFiles ?? []);
  const [existing, setExisting] = useState<"skip" | "update">("skip");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [err, setErr] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const run = async () => {
    if (!files.length) return input.current?.click();
    setBusy(true); setErr("");
    const body = new FormData();
    files.forEach((f) => body.append("files", f));
    body.append("existing", existing);
    try { setResult(await api.upload<Result>(url, body)); onDone(); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  return (
    <Modal title={<span className="row"><Icon name="contacts" size={16} /> Import contacts</span>} onClose={onClose}
      footer={<><span className="spacer" />
        {result ? <button className="btn primary" onClick={onClose}>Done</button> : <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy} onClick={run}>{busy ? "Importing…" : files.length ? "Import" : "Choose files…"}</button>
        </>}</>}>
      <div className="form">
        {!result && (<>
          <p className="muted small">vCard files (.vcf), with one contact or thousands: exported from a phone, Google Contacts, Outlook or iCloud.
            Everything on each card is kept (photos, extra numbers…) and goes to the address book with the next sync, so phones get them too.</p>
          <input ref={input} type="file" multiple accept=".vcf,.vcard,text/vcard,text/x-vcard" hidden
            onChange={(e) => { setFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
          <div className="drop-box" onClick={() => input.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); setFiles(Array.from(e.dataTransfer.files)); }}>
            {files.length
              ? files.map((f) => <div key={f.name}><Icon name="file" size={14} /> {f.name} <span className="muted small">({Math.ceil(f.size / 1024)} KB)</span></div>)
              : <><Icon name="upload" size={20} /><span>Choose .vcf files, or drop them here</span></>}
          </div>
          <div className="stack">A contact that's already here (same card, email, or name and phone)
            <label className="inline-check"><input type="radio" checked={existing === "skip"} onChange={() => setExisting("skip")} /> Skip it</label>
            {canUpdate && <label className="inline-check"><input type="radio" checked={existing === "update"} onChange={() => setExisting("update")} /> Update it with the file's details</label>}
          </div>
        </>)}
        {result && (
          <div className="import-result">
            <p><b>{result.created}</b> contact{result.created === 1 ? "" : "s"} added
              {result.updated ? <>, <b>{result.updated}</b> updated</> : null}
              {result.skipped ? <>, <b>{result.skipped}</b> already here (skipped)</> : null}.</p>
            {!!result.error_count && (
              <div className="error small">{result.error_count} couldn't be read:<ul>{result.errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div>
            )}
          </div>
        )}
        {err && <div className="error">{err}</div>}
      </div>
    </Modal>
  );
}
