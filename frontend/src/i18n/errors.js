/** Machine codes are translated; arbitrary server details are never rendered. */
export function apiErrorDescriptor(body, status) {
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
    "session.not_found": "errors.notFound",
    "session.closed": "errors.sessionClosed",
    "session.unknown_agent": "errors.unknownAgent",
    "mention.ambiguous": "errors.mentionAmbiguous",
    "mention.missing": "errors.mentionMissing",
    "mention.unavailable": "errors.mentionUnavailable",
    "mention.self": "errors.mentionSelf",
    "message.unavailable": "errors.messageUnavailable",
    "upload.invalid": "errors.uploadInvalid",
    "upload.too_large": "errors.uploadTooLarge",
    "upload.unsupported": "errors.uploadUnsupported",
    "upload.forbidden": "errors.uploadForbidden",
    "upload.excluded": "errors.sessionExcluded",
    "upload.incomplete": "errors.uploadIncomplete",
    "upload.failed": "errors.uploadFailed",
    "bridge.offline": "errors.bridgeOffline",
    "bridge.timeout": "errors.bridgeTimeout",
    "admin.unavailable": "errors.adminUnavailable",
    "admin.rejected": "errors.adminRejected",
    "transcript.not_found": "errors.transcriptNotFound",
    "transcript.unsupported": "errors.transcriptUnsupported",
    "transcript.invalid": "errors.invalidSession",
    "transcript.excluded": "errors.sessionExcluded",
    "transcript.failed": "errors.transcriptFailed",
    "attachment.not_found": "errors.attachmentMissing",
    "attachment.forbidden": "errors.attachmentForbidden",
    "attachment.unsupported": "errors.attachmentUnsupported",
    "attachment.too_large": "errors.attachmentTooLarge",
    "attachment.invalid": "errors.invalidSession",
    "attachment.excluded": "errors.sessionExcluded",
    "attachment.failed": "errors.attachmentFailed",
    "delivery.retry_unavailable": "errors.retryUnavailable",
    "delivery.cancel_unavailable": "errors.cancelUnavailable",
    "delivery.release_unavailable": "errors.releaseUnavailable",
    "schedule.time_in_past": "errors.scheduleTimeInPast",
    "schedule.too_far_ahead": "errors.scheduleTooFarAhead",
    "schedule.not_allowed": "errors.scheduleNotAllowed",
    "node.name_taken": "errors.nodeNameTaken",
    "node.not_found": "errors.notFound",
    "node.other_provider": "errors.nodeOtherProvider",
    "relation_type.name_taken": "errors.relationTypeTaken",
    "relation_type.not_found": "errors.notFound",
    "relation_type.in_use": "errors.relationTypeInUse",
    "relation.exists": "errors.relationExists",
    "relation.self": "errors.relationSelf",
    "relation.not_found": "errors.notFound",
    "return.empty": "errors.returnEmpty",
    "return.unavailable": "errors.returnUnavailable",
    "project.name_taken": "errors.projectNameTaken",
    "project.not_found": "errors.notFound",
    "project.node_not_in_project": "errors.nodeNotInProject",
    "claude_renew.failed": "errors.claudeRenewFailed",
    "stats.invalid_timezone": "errors.invalidTimezone",
    "stats.invalid_date": "errors.invalidDate",
    "stats.invalid_range": "errors.invalidRange",
    "stats.range_too_large": "errors.rangeTooLarge",
  };
  const key = Object.hasOwn(codes, body?.code) ? codes[body.code] : null;
  if (key) {
    // Only this documented parameter is interpolated; never pass arbitrary
    // server options such as lng/defaultValue into the translation engine.
    const id = Number.isSafeInteger(body?.params?.id) ? body.params.id : "?";
    return { key, params: { id } };
  }
  if (status === 401 || status === 403) return { key: "errors.unauthorized" };
  if (status === 404) return { key: "errors.notFound" };
  if (status === 422) return { key: "errors.validation" };
  return { key: "errors.operation", params: { status } };
}

export function apiErrorMessage(body, status, t) {
  const { key, params } = apiErrorDescriptor(body, status);
  return t(key, params);
}
