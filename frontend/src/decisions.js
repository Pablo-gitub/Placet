export const OTHER = "__altro__";

/**
 * Builds the decision body for a request from the card's local state.
 * Kept separate from the component so the rules mirror the backend
 * validation (RF-02, RF-03) in one readable place.
 */
export function buildDecision(req, status, { editedPrompt, selected, otherText, targetSession, deliveryMode, allowAutoReturn }) {
  const text = otherText.trim();
  const body = { status, selected_options: [] };

  if (req.question_type === "prompt_review") {
    if (status === "APPROVED" && editedPrompt !== undefined && editedPrompt.trim() !== "" && editedPrompt !== req.prompt) {
      body.edited_prompt = editedPrompt;
    }
    if (status === "APPROVED" && targetSession) {
      body.recipient_session_id = targetSession;
    }
    if (status === "APPROVED" && deliveryMode === "now") body.delivery_mode = "now";
    if (status === "APPROVED" && allowAutoReturn) body.allow_auto_return = true;
    return body;
  }

  if (req.question_type === "confirmation") {
    if (text) body.custom_text = text;
    return body;
  }

  // Choice questions: options plus the always-available "Altro".
  if (status === "REJECTED") {
    if (text) body.custom_text = text;
    return body;
  }
  body.selected_options = selected.filter((o) => o !== OTHER);
  if (selected.includes(OTHER) && text) body.custom_text = text;
  return body;
}

export function canApprove(req, { selected, otherText }) {
  if (req.question_type !== "single_choice" && req.question_type !== "multi_choice") {
    return true;
  }
  const otherOk = selected.includes(OTHER) && otherText.trim() !== "";
  const options = selected.filter((o) => o !== OTHER);
  return options.length > 0 || otherOk;
}

/** Id from a notification deep link such as "#request-42", or null. */
export function requestIdFromHash(hash) {
  const match = /^#request-(\d+)$/.exec(hash || "");
  return match ? Number(match[1]) : null;
}

/**
 * Where the PWA is: "#/" home, "#/storico" history, "#/sessione/<id>" a
 * session's conversation; "#request-N" (notification links) is resolved by
 * the app once the request is loaded.
 */
export function routeFromHash(hash) {
  const session = /^#\/sessione\/([A-Za-z0-9_-]+)(?:\?project=(\d+))?$/.exec(hash || "");
  if (session) return { view: "session", sessionId: session[1], projectId: session[2] ? Number(session[2]) : undefined };
  const project = /^#\/progetti\/(\d+)$/.exec(hash || "");
  if (project) return { view: "project_detail", projectId: Number(project[1]) };
  if (hash === "#/progetti" || hash === "#projects") return { view: "projects" };
  if (hash === "#/storico") return { view: "history" };
  if (hash === "#/statistiche") return { view: "statistics" };
  if (hash === "#/impostazioni") return { view: "settings" };
  if (hash === "#/comandi") return { view: "commands" };
  return { view: "home" };
}

export function needsReturnApproval(req) {
  return req.status === "COMPLETED" && req.sender_id !== "human" && req.recipient_id !== "human"
    && req.response && !req.delivered_at && !req.allow_auto_return && !req.return_decision;
}
