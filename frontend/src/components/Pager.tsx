import { useEffect, useState } from "react";
import { Icon } from "./Icon";

/** Views that list records one after another can be split into pages (a view setting). */
export const PAGED_VIEWS = new Set(["grid", "list", "gallery"]);
export const PAGE_SIZES = [10, 25, 50, 100, 500, 1000];

/** The records on the current page; back to page 1 whenever `resetKey` changes (other view, filter, search). */
export function usePaged<T>(items: T[], pageSize: number | undefined, resetKey: string) {
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [resetKey, pageSize]);
  if (!pageSize) return { items, page: 0, pages: 1, setPage, total: items.length, pageSize: 0 };
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const p = Math.min(page, pages - 1);  // records were deleted from the last page
  return { items: items.slice(p * pageSize, (p + 1) * pageSize), page: p, pages, setPage, total: items.length, pageSize };
}

export function Pager({ page, pages, total, pageSize, setPage }: { page: number; pages: number; total: number; pageSize: number; setPage: (p: number) => void }) {
  if (!pageSize || total <= pageSize) return null;
  const from = page * pageSize + 1, to = Math.min(total, (page + 1) * pageSize);
  return (
    <div className="pager" role="navigation" aria-label="Pages">
      <button className="icon-btn" disabled={page === 0} onClick={() => setPage(0)} aria-label="First page"><Icon name="left" size={14} /><Icon name="left" size={14} /></button>
      <button className="icon-btn" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Previous page"><Icon name="left" size={16} /></button>
      <span className="pager-text">{from}–{to} of {total}</span>
      <button className="icon-btn" disabled={page >= pages - 1} onClick={() => setPage(page + 1)} aria-label="Next page"><Icon name="right" size={16} /></button>
      <button className="icon-btn" disabled={page >= pages - 1} onClick={() => setPage(pages - 1)} aria-label="Last page"><Icon name="right" size={14} /><Icon name="right" size={14} /></button>
    </div>
  );
}
