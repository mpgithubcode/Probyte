import { useCallback, useEffect, useState } from "react";

/**
 * Windowing for fixed-height rows: only the rows in (or near) the visible part of the scroll
 * container are rendered, with spacer padding above and below, so tables with tens of thousands
 * of records stay fast. Attach `ref` to the scroll container; `offset` is the height of content
 * above the first row (e.g. a sticky header).
 */
export function useVirtualRows(count: number, rowHeight: number, offset = 0, overscan = 12) {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [view, setView] = useState({ top: 0, height: 800 });
  const ref = useCallback((node: HTMLElement | null) => setEl(node), []);

  useEffect(() => {
    if (!el) return;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setView({ top: el.scrollTop, height: el.clientHeight }));
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => { el.removeEventListener("scroll", update); ro.disconnect(); cancelAnimationFrame(frame); };
  }, [el]);

  const start = Math.min(count, Math.max(0, Math.floor((view.top - offset) / rowHeight) - overscan));
  const end = Math.max(start, Math.min(count, Math.ceil((view.top - offset + view.height) / rowHeight) + overscan));
  return { ref, el, start, end, padTop: start * rowHeight, padBottom: (count - end) * rowHeight };
}
