import { useState } from "react";
import { api } from "../api";
import type { Base, User, Workspace } from "../types";
import { PALETTE } from "../util";
import { Icon, tableIcon } from "./Icon";
import { Popover } from "./Overlay";

const COLLAPSED_KEY = "probyte.collapsedBases";

function loadCollapsed(): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? "[]")); } catch { return new Set(); }
}

export function Sidebar({ ws, user, active, open, onClose, onOpen, onNewTable, onNewCombo, onSettings, onTrash, onSearch, onLogout, onChanged, onError }: {
  onTrash: () => void;
  onSearch: () => void;
  ws: Workspace;
  user: User;
  active?: { kind: string; id: string };
  open: boolean;                       // drawer state on phones
  onClose: () => void;
  onOpen: (kind: "table" | "combo", id: string) => void;
  onNewTable: (baseId: string) => void;
  onNewCombo: (baseId: string) => void;
  onSettings: () => void;
  onLogout: () => void;
  onChanged: () => void;
  onError: (msg: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const [menu, setMenu] = useState<{ base: Base; anchor: HTMLElement } | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showHidden, setShowHidden] = useState<Set<string>>(new Set());  // bases whose hidden tables are shown
  const is = (kind: string, id: string) => active?.kind === kind && active.id === id;

  const toggle = (id: string) => setCollapsed((c) => {
    const next = new Set(c);
    if (next.has(id)) next.delete(id); else next.add(id);
    try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next])); } catch { /* private mode */ }
    return next;
  });

  const newBase = async () => {
    try {
      const b = await api.post<Base>("/bases", { name: "New base" });
      onChanged();
      setRenaming(b.id);
    } catch (e) { onError((e as Error).message); }
  };
  const patchBase = async (b: Base, p: Partial<Base>) => {
    try { await api.patch(`/bases/${b.id}`, p); onChanged(); } catch (e) { onError((e as Error).message); }
  };
  const deleteBase = async (b: Base) => {
    try { await api.del(`/bases/${b.id}`); setMenu(null); onChanged(); } catch (e) { onError((e as Error).message); }
  };

  return (
    <>
      {open && <div className="drawer-backdrop" onClick={onClose} />}
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand">
          <span className="logo">P</span>
          <span>Probyte</span>
          <span className="spacer" />
          <button className="icon-btn drawer-close" onClick={onClose} aria-label="Close menu"><Icon name="x" /></button>
        </div>

        <div className="side-head">
          <span>Bases</span>
          <button className="icon-btn" title="Search everything (Ctrl K)" aria-label="Search everything" onClick={onSearch}><Icon name="search" size={14} /></button>
          <button className="icon-btn" title="New base" onClick={newBase}><Icon name="plus" size={14} /></button>
        </div>

        <div className="side-scroll">
          {ws.bases.map((b) => {
            // hidden tables stay out of the list (unless open, or the base's hidden ones are shown)
            const reveal = showHidden.has(b.id);
            const allTables = ws.tables.filter((t) => t.base_id === b.id);
            const allCombos = ws.combos.filter((c) => c.base_id === b.id);
            const tables = allTables.filter((t) => !t.hidden || reveal || is("table", t.id));
            const combos = allCombos.filter((c) => !c.hidden || reveal || is("combo", c.id));
            const nHidden = allTables.filter((t) => t.hidden).length + allCombos.filter((c) => c.hidden).length;
            const isCollapsed = collapsed.has(b.id);
            return (
              <div key={b.id} className="base">
                <div className="base-row">
                  <button className="base-toggle" onClick={() => toggle(b.id)} aria-expanded={!isCollapsed}>
                    <span className={`chev ${isCollapsed ? "" : "open"}`}><Icon name="right" size={12} /></span>
                    <span className="base-dot" style={{ background: b.color }}>{b.name[0]?.toUpperCase()}</span>
                    {renaming === b.id ? (
                      <input className="input sm base-rename" autoFocus defaultValue={b.name} onClick={(e) => e.stopPropagation()}
                        onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== b.name) patchBase(b, { name: v }); setRenaming(null); }}
                        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") setRenaming(null); }} />
                    ) : <span className="base-name">{b.name}</span>}
                  </button>
                  <button className="icon-btn base-more" title="Base options"
                    onClick={(e) => { setConfirmDelete(false); setMenu({ base: b, anchor: e.currentTarget }); }}>
                    <Icon name="more" size={14} />
                  </button>
                </div>
                {!isCollapsed && (
                  <div className="base-items">
                    {tables.map((t) => (
                      <button key={t.id} className={`side-item ${is("table", t.id) ? "active" : ""} ${t.hidden ? "is-hidden" : ""}`} onClick={() => onOpen("table", t.id)}>
                        {t.icon ? <span className="emoji-icon">{t.icon}</span> : <Icon name={tableIcon(t.kind)} size={15} />}
                        <span className="side-name">{t.name}</span>
                        {t.kind === "remote"
                          ? t.remote?.error && <span className="side-dot err" title={t.remote.error} />
                          : t.kind !== "generic" && <span className={`side-dot ${t.last_sync_error ? "err" : ""}`} title={t.last_sync_error ?? "CalDAV synced"} />}
                      </button>
                    ))}
                    {combos.map((c) => (
                      <button key={c.id} className={`side-item ${is("combo", c.id) ? "active" : ""} ${c.hidden ? "is-hidden" : ""}`} onClick={() => onOpen("combo", c.id)}>
                        {c.icon ? <span className="emoji-icon">{c.icon}</span> : <Icon name="layers" size={15} />}
                        <span className="side-name">{c.name}</span>
                        <span className="side-swatches">{c.sources.map((s) => <span key={s.table_id} style={{ background: s.color }} />)}</span>
                      </button>
                    ))}
                    {!allTables.length && !allCombos.length && <div className="muted small side-empty-note">No tables yet</div>}
                    {nHidden > 0 && (
                      <button className="side-add" onClick={() => setShowHidden((s) => { const n = new Set(s); if (reveal) n.delete(b.id); else n.add(b.id); return n; })}>
                        <Icon name="eye" size={13} /> {reveal ? "Hide hidden" : `Show ${nHidden} hidden`}
                      </button>
                    )}
                    <button className="side-add" onClick={() => onNewTable(b.id)}><Icon name="plus" size={13} /> New table</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="side-user">
          <button className="side-item" onClick={onSettings} title="Settings, sync accounts, public links, API">
            <span className="avatar">{user.username[0].toUpperCase()}</span>
            <span className="side-name">{user.username}</span>
            <Icon name="settings" size={14} />
          </button>
          <button className="icon-btn" title="Trash" aria-label="Trash" onClick={onTrash}><Icon name="trash" size={15} /></button>
          <button className="icon-btn" title="Sign out" onClick={onLogout}><Icon name="logout" size={15} /></button>
        </div>
      </aside>

      {menu && (
        <Popover anchor={menu.anchor} onClose={() => setMenu(null)} width={220}>
          <div className="menu-list">
            <button className="menu-item" onClick={() => { onNewTable(menu.base.id); setMenu(null); }}><Icon name="table" size={14} /> New table</button>
            <button className="menu-item" onClick={() => { onNewCombo(menu.base.id); setMenu(null); }}><Icon name="layers" size={14} /> New combined database</button>
            <button className="menu-item" onClick={() => { setRenaming(menu.base.id); setMenu(null); }}><Icon name="edit" size={14} /> Rename</button>
            <button className="menu-item" title={menu.base.id} onClick={() => {
              const id = menu.base.id;
              navigator.clipboard?.writeText(id).then(() => onError(`Copied base ID ${id}`), () => onError(id));
              setMenu(null);
            }}><Icon name="copy" size={14} /> Copy base ID</button>
            <div className="swatch-row">
              {PALETTE.slice(0, 8).map((c) => (
                <button key={c} className={`swatch-btn ${menu.base.color === c ? "on" : ""}`} style={{ background: c }}
                  title="Base color" onClick={() => { patchBase(menu.base, { color: c }); setMenu(null); }} />
              ))}
            </div>
            {ws.bases.length > 1 && (confirmDelete ? (
              <button className="menu-item danger" onClick={() => deleteBase(menu.base)}><Icon name="trash" size={14} /> Delete base and its tables</button>
            ) : (
              <button className="menu-item danger" onClick={() => setConfirmDelete(true)}><Icon name="trash" size={14} /> Delete base…</button>
            ))}
          </div>
        </Popover>
      )}
    </>
  );
}
