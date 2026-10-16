import { useEffect, useState } from "react";

/** Two columns from this width up (desktop, tablet landscape). */
export const WIDE_QUERY = "(min-width: 900px)";

function matches() {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(WIDE_QUERY).matches
    : false;
}

/** Whether the screen is wide enough for the two-column layout. */
export function useWide() {
  const [wide, setWide] = useState(matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const query = window.matchMedia(WIDE_QUERY);
    const onChange = () => setWide(query.matches);
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);
  return wide;
}
