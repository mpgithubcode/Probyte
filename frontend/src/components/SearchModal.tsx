import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { debounce } from "../util";
import { Icon } from "./Icon";
import { Modal } from "./Overlay";

interface Hit { base: string; table: string; table_id: string; id: string; title: string; matched_field: string; match: string }

/** Ctrl/⌘ K: find records in every table (titles and any other field), and open one. */
export function SearchModal({ onClose, onOpen }: { onClose: () => void; onOpen: (tableId: string, recordId: string) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [at, setAt] = useState(0);
  const [error, setError] = useState("");
  const latest = useRef("");
  const run = useRef(debounce((text: string) => {
    if (!text.trim()) { setHits(null); return; }
    api.get<{ results: Hit[] }>(`/v1/search?q=${encodeURIComponent(text)}&limit=50`)
      .then((r) => { if (latest.current === text) { setHits(r.results); setAt(0); } })
      .catch((e) => setError(e.message));
  }, 200)).current;
  useEffect(() => { latest.current = q; setError(""); run(q); }, [q, run]);

  const pick = (h?: Hit) => { if (h) { onOpen(h.table_id, h.id); onClose(); } };
  return (
    <Modal title="Search everything" width={620} onClose={onClose}>
      <div className="form search-modal">
        <div className="search big">
          <Icon name="search" size={16} />
          <input autoFocus placeholder="Find a record in any table…" value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setAt((i) => Math.min((hits?.length ?? 1) - 1, i + 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setAt((i) => Math.max(0, i - 1)); }
              if (e.key === "Enter") pick(hits?.[at]);
            }} />
        </div>
        {error && <div className="error">{error}</div>}
        {hits && !hits.length && <p className="muted">Nothing found for “{q}”.</p>}
        <div className="search-hits" role="listbox">
          {hits?.map((h, i) => (
            <button key={h.id} role="option" aria-selected={i === at} className={`search-hit ${i === at ? "active" : ""}`}
              onMouseEnter={() => setAt(i)} onClick={() => pick(h)}>
              <span className="search-hit-title">{h.title || "Untitled"}</span>
              <span className="muted small">{h.base} › {h.table}
                {h.matched_field && h.match !== h.title && <> · {h.matched_field}: <i>{h.match}</i></>}</span>
            </button>
          ))}
        </div>
        {!hits && <p className="muted small">Tip: press Ctrl K (⌘ K on a Mac) anywhere to search.</p>}
      </div>
    </Modal>
  );
}
