// Display labels live in the locale catalogs; this module contains state predicates.
export const isUncertain = (req) => req.status === "DELIVERING" && req.delivery === "uncertain";

// After a reply the turn normally ends within seconds; only then is a turn
// end still unseen worth the user's attention.
export const REPLY_TURN_GRACE_MS = 2 * 60 * 1000;

/**
 * A delivered request still holding its session's queue whose end Placet
 * cannot see: the turn was not seen starting in time, it is stalled, or it
 * was answered but its turn end never arrived. It mirrors the backend's
 * in-flight rule; only the user moves the queue on (RF-29b).
 */
export const holdsQueueUnseen = (req, now = Date.now()) =>
  Boolean(req.recipient_session_id) &&
  !req.queue_released_at &&
  ["EXECUTING", "STALLED", "COMPLETED"].includes(req.status) &&
  ["unobserved", "started"].includes(req.execution) &&
  (Boolean(req.start_unconfirmed_at) || req.status === "STALLED" ||
    (req.status === "COMPLETED" && Boolean(req.completed_at) &&
      now - Date.parse(req.completed_at) >= REPLY_TURN_GRACE_MS));
