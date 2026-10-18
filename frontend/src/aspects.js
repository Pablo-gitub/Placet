// Display labels live in the locale catalogs; this module contains state predicates.
export const isUncertain = (req) => req.status === "DELIVERING" && req.delivery === "uncertain";

/**
 * A delivered request still holding its session's queue whose end Placet
 * cannot see: the turn was not seen starting in time, or it is stalled.
 * Only the user moves the queue on (RF-29b).
 */
export const holdsQueueUnseen = (req) =>
  Boolean(req.recipient_session_id) &&
  !req.queue_released_at &&
  ["EXECUTING", "STALLED", "COMPLETED"].includes(req.status) &&
  ["unobserved", "started"].includes(req.execution) &&
  (Boolean(req.start_unconfirmed_at) || req.status === "STALLED");
