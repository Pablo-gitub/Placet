/** RF-44: live activity of the relations, as the diagram draws it. */

/**
 * What a request on a relation is doing now, as the graph shows it:
 * "waiting" for the user (ball still halfway), "delivering" (ball A -> B),
 * "executing" (edge lit, the recipient pulses), "returning" (ball B -> A).
 */
export function requestActivity(req) {
  if (req.status === "PENDING") return "waiting";
  if (req.status === "APPROVED" || req.status === "DELIVERING") return "delivering";
  if (req.status === "EXECUTING") return "executing";
  if (req.status === "COMPLETED" && req.response && !req.delivered_at && !req.return_decision) {
    return req.allow_auto_return ? "returning" : "waiting";
  }
  return null;
}

// The most telling state wins when several requests share a relation.
const ACTIVITY_RANK = { waiting: 4, delivering: 3, returning: 2, executing: 1 };

export function edgeActivities(requests) {
  const out = {};
  for (const req of requests) {
    const activity = req.relation_id != null && requestActivity(req);
    if (activity && (ACTIVITY_RANK[activity] > (ACTIVITY_RANK[out[req.relation_id]] || 0))) out[req.relation_id] = activity;
  }
  return out;
}
