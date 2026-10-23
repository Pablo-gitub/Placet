/** RF-44: live activity of the relations, as the diagram draws it. */

/**
 * What a request on a relation is doing now, as the graph shows it:
 * "waiting" for the user (ball still halfway), "delivering" (ball A -> B,
 * only while the delivery is really in progress), "queued" (approved but not
 * being delivered: held behind another request, recipient busy or
 * unreachable; ball still at the sender's end), "executing" (edge lit, the
 * recipient pulses), "returning" (ball B -> A).
 */
const isScheduledAhead = (req) => !!req.scheduled_at && new Date(req.scheduled_at).getTime() > Date.now();

export function requestActivity(req) {
  if (req.status === "PENDING") return "waiting";
  if (req.status === "DELIVERING") return "delivering";
  // Approved but held for a time that has not come: not stuck, it leaves at scheduled_at.
  if (req.status === "APPROVED") return isScheduledAhead(req) ? "scheduled" : "queued";
  if (req.status === "EXECUTING") return "executing";
  if (req.status === "COMPLETED" && req.response && !req.delivered_at && !req.return_decision) {
    return req.allow_auto_return ? "returning" : "waiting";
  }
  return null;
}

// The most telling state wins when several requests share a relation. Queued
// ranks just above executing: a reply on its way back says more than a
// message waiting its turn, and a message waiting behind a running request
// is still worth showing.
const ACTIVITY_RANK = { waiting: 5, delivering: 4, returning: 3, queued: 2, executing: 1, scheduled: 0.5 };

export function edgeActivities(requests) {
  const out = {};
  for (const req of requests) {
    const activity = req.relation_id != null && requestActivity(req);
    if (activity && (ACTIVITY_RANK[activity] > (ACTIVITY_RANK[out[req.relation_id]] || 0))) out[req.relation_id] = activity;
  }
  return out;
}

/** The earliest delivery time of the scheduled requests of each relation, for the tooltip. */
export function edgeSchedules(requests) {
  const out = {};
  for (const req of requests) {
    if (req.relation_id == null || requestActivity(req) !== "scheduled") continue;
    if (!out[req.relation_id] || new Date(req.scheduled_at) < new Date(out[req.relation_id])) out[req.relation_id] = req.scheduled_at;
  }
  return out;
}
