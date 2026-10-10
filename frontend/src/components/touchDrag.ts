import type { PointerEvent as RPointerEvent } from "react";

/** How long an item has to be held on a touch screen before it can be dragged. */
export const HOLD_MS = 3000;
const SLOP = 10;  // px a finger may wander while holding

interface HoldDrag {
  onTap?: () => void;                         // released before the hold completed, without moving
  onStart: (x: number, y: number) => void;    // held long enough: dragging begins
  onMove?: (x: number, y: number) => void;
  onEnd?: (x: number, y: number) => void;
}

/**
 * Touch-friendly dragging: on a touch screen the item only starts dragging after being held still for HOLD_MS,
 * so swiping over it scrolls the page as usual and a quick tap still opens it. Returns false for mouse/pen
 * (the caller drags right away as before).
 */
export function holdToDrag(e: RPointerEvent, h: HoldDrag): boolean {
  if (e.pointerType !== "touch") return false;
  const x0 = e.clientX, y0 = e.clientY;
  let x = x0, y = y0, active = false, done = false;
  const blockScroll = (ev: TouchEvent) => { if (active) ev.preventDefault(); };
  const timer = window.setTimeout(() => {
    if (done) return;
    active = true;
    navigator.vibrate?.(30);
    h.onStart(x, y);
  }, HOLD_MS);
  const move = (ev: PointerEvent) => {
    x = ev.clientX; y = ev.clientY;
    if (!active) { if (Math.hypot(x - x0, y - y0) > SLOP) cleanup(); return; }  // a swipe: let the page scroll
    h.onMove?.(x, y);
  };
  const up = (ev: PointerEvent) => {
    const wasActive = active;
    cleanup();
    if (wasActive) h.onEnd?.(ev.clientX, ev.clientY);
    else if (Math.hypot(ev.clientX - x0, ev.clientY - y0) <= SLOP) h.onTap?.();
  };
  function cleanup() {
    done = true;
    window.clearTimeout(timer);
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", cleanup);
    document.removeEventListener("touchmove", blockScroll);
    active = false;
  }
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", cleanup);
  document.addEventListener("touchmove", blockScroll, { passive: false });  // once dragging, the finger moves the item, not the page
  return true;
}
