import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fileExt } from "../files";
import type { Attachment } from "../types";
import { Icon } from "./Icon";

/** ONLYOFFICE Document Server editing inside the file viewer (backend/app/office.py has the whole story). */

export interface OfficeStatus { enabled: boolean; url: string | null; editable: string[]; viewable: string[] }
export interface OfficeSession { session: string; mode: "edit" | "view"; api_url: string; config: Record<string, unknown> }

let statusPromise: Promise<OfficeStatus> | null = null;
export function useOfficeStatus() {
  const [s, setS] = useState<OfficeStatus | null>(null);
  useEffect(() => {
    statusPromise ??= api.get<OfficeStatus>("/office/status").catch(() => ({ enabled: false, url: null, editable: [], viewable: [] }));
    statusPromise.then(setS);
  }, []);
  return s;
}
export const resetOfficeStatus = () => { statusPromise = null; };

/** What the document server can do with a file of a files table: "edit", "view" or nothing. */
export function officeMode(status: OfficeStatus | null, item: Attachment): "edit" | "view" | null {
  if (!status?.enabled || !item.file) return null;
  const e = fileExt(item.name);
  return status.editable.includes(e) ? "edit" : status.viewable.includes(e) ? "view" : null;
}

declare global {
  interface Window { DocsAPI?: { DocEditor: new (id: string, config: Record<string, unknown>) => { destroyEditor: () => void } } }
}

/** Whether the editor still has edits it hasn't sent to the document server (a forcesave only saves what it has). */
const unsent = new Map<string, boolean>();
async function waitSent(session: string, timeoutMs = 15000) {
  const end = Date.now() + timeoutMs;
  while (unsent.get(session) && Date.now() < end) await new Promise((ok) => setTimeout(ok, 200));
}

const scripts = new Map<string, Promise<void>>();
function loadScript(src: string): Promise<void> {
  if (!scripts.has(src)) {
    scripts.set(src, new Promise((ok, fail) => {
      const el = document.createElement("script");
      el.src = src;
      el.async = true;
      el.onload = () => (window.DocsAPI ? ok() : fail(new Error("The document server's editor didn't load")));
      el.onerror = () => { scripts.delete(src); fail(new Error("Can't reach the document server from this browser")); };
      document.head.appendChild(el);
    }));
  }
  return scripts.get(src)!;
}

/** The editor itself. onDirty(true) when there are changes not yet written to the storage (the viewer decides when
 * they are: after a save it confirms). */
export function OfficeEditor({ session, onModified, onError }: {
  session: OfficeSession; onModified: () => void; onError: (msg: string) => void;
}) {
  const id = useRef(`office-${session.session}`).current;
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [msg, setMsg] = useState("");
  const latest = useRef({ onModified, onError });
  latest.current = { onModified, onError };
  useEffect(() => {
    let editor: { destroyEditor: () => void } | null = null;
    let live = true;
    loadScript(session.api_url).then(() => {
      if (!live || !window.DocsAPI) return;
      editor = new window.DocsAPI.DocEditor(id, {
        ...session.config,
        events: {
          onDocumentReady: () => live && setState("ready"),
          onDocumentStateChange: (e: { data: boolean }) => {  // true: edits made here; false: they reached the server
            unsent.set(session.session, e.data);
            if (e.data) latest.current.onModified();
          },
          onError: (e: { data?: { errorDescription?: string; errorCode?: number } }) => {
            const m = e.data?.errorDescription || `The document server reported an error (${e.data?.errorCode ?? "?"})`;
            latest.current.onError(m);
          },
        },
      });
      setTimeout(() => live && setState((s) => (s === "loading" ? "ready" : s)), 15000);
    }).catch((e) => { if (live) { setState("error"); setMsg(e.message); latest.current.onError(e.message); } });
    return () => { live = false; unsent.delete(session.session); try { editor?.destroyEditor(); } catch { /* already gone */ } };
  }, [session.session]);
  return (
    <div className="office-editor">
      {state === "loading" && <div className="viewer-msg office-loading"><Icon name="sync" size={18} className="spin" /> Opening ONLYOFFICE…</div>}
      {state === "error" && <div className="viewer-msg error">{msg}</div>}
      <div id={id} className="office-frame" />
    </div>
  );
}

export async function startOffice(item: Attachment, mode: "edit" | "view"): Promise<OfficeSession> {
  return api.post<OfficeSession>("/office/sessions", { record_id: item.id, mode });
}

/** Ask the document server to save now; resolves when the new version is in the storage (or nothing had changed). */
export async function saveOffice(session: OfficeSession, timeoutMs = 90000): Promise<void> {
  await waitSent(session.session);
  const before = await api.get<{ saves: number }>(`/office/sessions/${session.session}`);
  const r = await api.post<{ saving: boolean; unchanged?: boolean }>(`/office/sessions/${session.session}/save`);
  if (!r.saving) return;
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    await new Promise((ok) => setTimeout(ok, 800));
    const s = await api.get<{ saves: number; error?: string | null }>(`/office/sessions/${session.session}`);
    if (s.saves > before.saves) return;
    if (s.error) throw new Error(s.error);
  }
  throw new Error("The document server didn't send the saved document in time; try Save again");
}
