import { useEffect, useState } from "react";
import { REPLY_TURN_GRACE_MS } from "./aspects";

/**
 * The time the queue-hold predicate is evaluated at. A hold for an answered
 * request becomes visible only after a grace period, when no event may arrive
 * (that is the point: the turn end was lost), so re-render at that moment.
 * Runs only while the page is visible; returning to it re-evaluates at once.
 */
export default function useHoldClock(requests, sessionId) {
  const [now, setNow] = useState(() => Date.now());
  const pending = requests
    .filter((r) => r.recipient_session_id === sessionId && !r.queue_released_at && r.status === "COMPLETED"
      && ["unobserved", "started"].includes(r.execution) && r.completed_at)
    .map((r) => Date.parse(r.completed_at) + REPLY_TURN_GRACE_MS)
    .filter((at) => at > now);
  const next = pending.length ? Math.min(...pending) : null;

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") setNow(Date.now());
    };
    document.addEventListener("visibilitychange", onVisible);
    let timer = null;
    if (next !== null && document.visibilityState !== "hidden") {
      timer = setTimeout(() => setNow(Date.now()), Math.max(0, next - Date.now()));
    }
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      if (timer !== null) clearTimeout(timer);
    };
  }, [next]);

  return now;
}
