import { useEffect, useState } from "react";
import { api } from "../api";
import { attachmentUrl, fileExt, textUrl, thumbUrl, viewOf } from "../files";
import type { Attachment } from "../types";
import { formatBytes } from "../util";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

const MAX_TEXT = 2 * 1024 * 1024;  // larger text files are downloaded rather than shown

/** A file shown in the page: image, PDF, video, audio or a text file's source (editable for files of files tables). */
export function FileView({ item, editable, onSaved }: { item: Attachment; editable?: boolean; onSaved?: () => void }) {
  const view = viewOf(item);
  const [text, setText] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const big = item.size > MAX_TEXT;

  useEffect(() => {
    setText(null); setDraft(null); setErr("");
    if (view !== "text" || big) return;
    let live = true;
    fetch(textUrl(item)).then(async (r) => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({ detail: r.statusText }))).detail);
      return r.text();
    }).then((t) => { if (live) setText(t); }).catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [item.id, item.size, view, big]);

  const save = async () => {
    setBusy(true);
    try { await api.put(`/files/${item.id}/text`, { text: draft }); setText(draft); setDraft(null); onSaved?.(); }
    catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  const url = attachmentUrl(item);
  switch (view) {
    case "image": return <div className="file-view image"><img src={url} alt={item.name} /></div>;
    case "pdf": return <div className="file-view pdf"><iframe src={url} title={item.name} /></div>;
    case "video": return <div className="file-view media"><video src={url} controls preload="metadata" /></div>;
    case "audio": return <div className="file-view audio"><audio src={url} controls preload="metadata" /></div>;
    case "text":
      if (big) return <NoPreview item={item} why="This file is too large to show here." />;
      return (
        <div className="file-view text">
          {err && <div className="error small">{err}</div>}
          {text === null && !err && <div className="muted pad">Loading…</div>}
          {text !== null && (draft === null
            ? <pre className="file-text" onDoubleClick={() => editable && setDraft(text)}>{text || <span className="muted">(empty)</span>}</pre>
            : <textarea className="file-text input" autoFocus value={draft} spellCheck={false} onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(); } }} />)}
          {editable && text !== null && (
            <div className="row file-text-actions">
              {draft === null
                ? <button className="btn sm" onClick={() => setDraft(text)}><Icon name="edit" size={14} /> Edit</button>
                : <>
                    <button className="btn sm primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
                    <button className="btn sm" onClick={() => setDraft(null)}>Cancel</button>
                    <span className="muted small">Ctrl/⌘ S saves</span>
                  </>}
            </div>
          )}
        </div>
      );
    default: return <NoPreview item={item} />;
  }
}

function NoPreview({ item, why }: { item: Attachment; why?: string }) {
  const thumb = thumbUrl(item);
  return (
    <div className="file-view none">
      {thumb ? <img className="file-cover" src={thumb} alt="" /> : <span className="file-ext big">{fileExt(item.name)}</span>}
      <span className="muted">{why ?? "No preview for this kind of file."}</span>
      <a className="btn sm" href={attachmentUrl(item, true)}><Icon name="download" size={14} /> Download</a>
    </div>
  );
}

/** Files in a dialog, with previous / next. */
export function FilePreview({ items, index, onClose, editable, onSaved }: {
  items: Attachment[]; index: number; onClose: () => void; editable?: boolean; onSaved?: () => void;
}) {
  const [i, setI] = useState(index);
  const item = items[i];
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("textarea, input")) return;
      if (e.key === "ArrowRight" && i < items.length - 1) setI(i + 1);
      if (e.key === "ArrowLeft" && i > 0) setI(i - 1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [i, items.length]);
  if (!item) return null;
  return (
    <Modal width={980} onClose={onClose}
      title={<span className="row file-preview-title"><span className="ellipsis">{item.name}</span> <span className="muted small">{formatBytes(item.size)}</span></span>}
      footer={<>
        {items.length > 1 && <>
          <button className="btn sm" disabled={i === 0} onClick={() => setI(i - 1)} aria-label="Previous file"><Icon name="left" size={14} /></button>
          <span className="muted small">{i + 1} / {items.length}</span>
          <button className="btn sm" disabled={i === items.length - 1} onClick={() => setI(i + 1)} aria-label="Next file"><Icon name="right" size={14} /></button>
        </>}
        <span className="spacer" />
        {viewOf(item) && viewOf(item) !== "text" && <a className="btn sm" href={attachmentUrl(item)} target="_blank" rel="noreferrer"><Icon name="external" size={14} /> Open</a>}
        <a className="btn sm" href={attachmentUrl(item, true)}><Icon name="download" size={14} /> Download</a>
      </>}>
      <FileView item={item} editable={editable && item.file} onSaved={onSaved} />
    </Modal>
  );
}
