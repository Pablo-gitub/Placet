/** The user may still cancel or redirect a prompt to an agent that has not started to be delivered. */
export function isUndelivered(req) {
  return !!req && req.recipient_id !== "human"
    && (req.status === "PENDING" || (req.status === "APPROVED" && (req.delivery || "waiting") === "waiting"));
}


/** The first line of the message, for the compact row. */
export function previewOf(req) {
  const line = (req.edited_prompt || req.prompt || "").split("\n").map((l) => l.trim()).find(Boolean) || "";
  return line.replace(/^[#>*\-\s]+/, "");
}

