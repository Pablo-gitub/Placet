/** Machine codes are translated; arbitrary server details are never rendered. */
export function apiErrorMessage(body, status, t) {
  const codes = {
    "return.empty": "errors.returnEmpty",
    "return.unavailable": "errors.returnUnavailable",
  };
  const key = Object.hasOwn(codes, body?.code) ? codes[body.code] : null;
  if (key) return t(key);
  if (status === 401 || status === 403) return t("errors.unauthorized");
  if (status === 404) return t("errors.notFound");
  if (status === 422) return t("errors.validation");
  return t("errors.operation", { status });
}
