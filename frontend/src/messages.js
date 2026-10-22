/** The user may still cancel or redirect a prompt to an agent that has not started to be delivered. */
export function isUndelivered(req) {
  return !!req && req.recipient_id !== "human"
    && (req.status === "PENDING" || (req.status === "APPROVED" && (req.delivery || "waiting") === "waiting"));
}

