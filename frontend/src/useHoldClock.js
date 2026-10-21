import { useEffect, useState } from "react";
import { REPLY_TURN_GRACE_MS } from "./aspects";

const pageVisible = () => document.visibilityState !== "hidden";

/**
 * The time the queue-hold predicate is evaluated at. A hold for an answered
 * request becomes visible only after a grace period, when no event may arrive
 * (that is the point: the turn end was lost), so re-render at that moment.
 * The timer runs only while the page is visible: hiding the page cancels it,
 * showing it again re-reads the clock and arms the next deadline.
 */
export default function useHoldClock(requests, sessionId) {
  const [now, setNow] = useState(() => Date.now());
  const [visible, setVisible] = useState(pageVisible);
  const pending = requests
    .filter((r) => r.recipient_session_id === sessionId && !r.queue_released_at && r.status === "COMPLETED"
      && ["unobserved", "started"].includes(r.execution) && r.completed_at)
    .map((r) => Date.parse(r.completed_at) + REPLY_TURN_GRACE_MS)
    .filter((at) => at > now);
  const next = pending.length ? Math.min(...pending) : null;

  useEffect(() => {
    const onChange = () => {
      const shown = pageVisible();
      setVisible(shown);
      if (shown) setNow(Date.now());
    };
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  useEffect(() => {
    if (next === null || !visible) return undefined;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, next - Date.now()));
    return () => clearTimeout(timer);
  }, [next, visible]);

  return now;
}
