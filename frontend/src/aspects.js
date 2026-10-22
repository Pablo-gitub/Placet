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

/**
 * A session whose queue is held (see above) with an approved message waiting
 * behind it: what the Home list flags and the held-queue push is about.
 */
export const queueStuck = (requests, sessionId, now = Date.now()) =>
  requests.some((r) => r.recipient_session_id === sessionId && holdsQueueUnseen(r, now)) &&
  requests.some((r) => r.recipient_session_id === sessionId && r.status === "APPROVED" && (!r.scheduled_at || Date.parse(r.scheduled_at) <= now));

/**
 * RF-27: "missing" (the provider's configuration has no Placet MCP server) or
 * "suspect" (a Placet turn ended and the session never used it). Anything
 * else, including "unknown", is not worth a warning.
 */
export const mcpWarning = (session) =>
  session && (session.mcp_state === "missing" || session.mcp_state === "suspect") ? session.mcp_state : null;
