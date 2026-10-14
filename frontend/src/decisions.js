export const OTHER = "__altro__";

/**
 * Builds the decision body for a request from the card's local state.
 * Kept separate from the component so the rules mirror the backend
 * validation (RF-02, RF-03) in one readable place.
 */
export function buildDecision(req, status, { editedPrompt, selected, otherText, targetSession }) {
  const text = otherText.trim();
  const body = { status, selected_options: [] };

  if (req.question_type === "prompt_review") {
    if (status === "APPROVED" && editedPrompt !== undefined && editedPrompt.trim() !== "" && editedPrompt !== req.prompt) {
      body.edited_prompt = editedPrompt;
    }
    if (status === "APPROVED" && targetSession) {
      body.recipient_session_id = targetSession;
    }
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
