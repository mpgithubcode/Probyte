import { ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Icon";

// Open dialogs, newest last: Escape closes only the top one when dialogs are stacked.
const modalStack: object[] = [];

export function Modal({ title, onClose, children, footer, width = 520, drawer, docked, actions, className = "" }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; width?: number;
  drawer?: boolean;  // a panel on the right instead of a centred dialog
  docked?: boolean;  // a panel on the right that leaves the page usable (no backdrop: click other rows, scroll, edit)
  actions?: ReactNode;  // buttons in the header, before Close
  className?: string;
}) {
  const [token] = useState(() => ({}));
  useEffect(() => {
    modalStack.push(token);
    return () => { modalStack.splice(modalStack.indexOf(token), 1); };
  }, [token]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && modalStack[modalStack.length - 1] === token) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, token]);
  const head = (
    <div className="modal-head">
      <div className="modal-title">{title}</div>
      {actions}
      <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
    </div>
  );
  if (docked) {
    return createPortal(
      <div className={`modal docked-panel ${className}`} style={{ width }} role="dialog" aria-modal="false">
        {head}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>,
      document.body,
    );
  }
  return createPortal(
    <div className={`backdrop ${drawer ? "drawer-backdrop" : ""}`} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${drawer ? "drawer" : ""} ${className}`} style={{ width }} role="dialog">
        {head}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Popover({ anchor, onClose, children, align = "left", width, className = "" }: {
  anchor: HTMLElement | null; onClose: () => void; children: ReactNode;
  align?: "left" | "right"; width?: number; className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!anchor || !el) return;
    const place = () => {
      const r = anchor.getBoundingClientRect();
      const h = el.offsetHeight, w = el.offsetWidth;
      let top = r.bottom + 4;
      if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
      let left = align === "right" ? r.right - w : r.left;
      left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
      setPos((p) => (p && p.top === top && p.left === left ? p : { top, left }));
    };
    place();
    // placed again when what's in it changes size (a list that loads, a table that widens)
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(place) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, [anchor, align]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current?.contains(t) || anchor?.contains(t)) return;
      // Clicks inside a popover opened from this one shouldn't close it.
      if ((t as HTMLElement).closest?.(".popover")) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [anchor, onClose]);

  return createPortal(
    <div ref={ref} className={`popover ${className}`}
      style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999, width }}
      onMouseDown={(e) => e.stopPropagation()}>
      {children}
    </div>,
    document.body,
  );
}

/** Small hook for "button that toggles a popover". */
export function usePopover() {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  return {
    anchor,
    // Read currentTarget now: React may run the updater after the event, when it's already been cleared.
    open: (e: { currentTarget: HTMLElement }) => { const el = e.currentTarget; setAnchor((a) => (a ? null : el)); },
    close: () => setAnchor(null),
  };
}
