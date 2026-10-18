/** Machine codes are translated; arbitrary server details are never rendered. */
export function apiErrorMessage(body, status, t) {
  const codes = {
    "decision.not_pending": "errors.decisionNotPending",
    "decision.prompt_edit_not_allowed": "errors.promptEditNotAllowed",
    "decision.recipient_not_allowed": "errors.recipientNotAllowed",
    "decision.no_options": "errors.noOptions",
    "decision.duplicate_options": "errors.duplicateOptions",
    "decision.unknown_options": "errors.unknownOptions",
    "decision.rejected_options": "errors.rejectedOptions",
    "decision.single_choice": "errors.singleChoice",
    "decision.multi_choice": "errors.multiChoice",
    "decision.self_target": "errors.selfTarget",
    "decision.now_target": "errors.nowTarget",
    "session.unavailable": "errors.sessionUnavailable",
    "session.excluded": "errors.sessionExcluded",
    "return.empty": "errors.returnEmpty",
    "return.unavailable": "errors.returnUnavailable",
  };
  const key = Object.hasOwn(codes, body?.code) ? codes[body.code] : null;
  if (key) {
    // Only this documented parameter is interpolated; never pass arbitrary
    // server options such as lng/defaultValue into the translation engine.
    const id = Number.isSafeInteger(body?.params?.id) ? body.params.id : "?";
    return t(key, { id });
  }
  if (status === 401 || status === 403) return t("errors.unauthorized");
  if (status === 404) return t("errors.notFound");
  if (status === 422) return t("errors.validation");
  return t("errors.operation", { status });
}
