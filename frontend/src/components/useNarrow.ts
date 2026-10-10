import { useEffect, useState } from "react";

const query = typeof window !== "undefined" ? window.matchMedia("(max-width: 760px)") : null;

/** True on phone-sized screens (matches the CSS mobile breakpoint). */
export function useNarrow() {
  const [narrow, setNarrow] = useState(!!query?.matches);
  useEffect(() => {
    const on = () => setNarrow(!!query?.matches);
    query?.addEventListener("change", on);
    return () => query?.removeEventListener("change", on);
  }, []);
  return narrow;
}

/** True on touch screens without a mouse pointer (no hover, no drag and drop). */
export const isTouch = typeof window !== "undefined" && window.matchMedia("(hover: none)").matches;
