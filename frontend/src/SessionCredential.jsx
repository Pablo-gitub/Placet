import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

async function call(method, body) {
  const res = await fetch("/api/settings/session-credentials", {
    method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** RS-09: whether new Claude Code sessions get a verified session credential (the coordinator view needs it). */
export default function SessionCredential() {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(null);

  useEffect(() => {
    call("GET").then((r) => setEnabled(!!r.enabled)).catch(() => {});
  }, []);

  const toggle = async () => {
    const next = !enabled;
    setEnabled(next);
    try { setEnabled((await call("PUT", { enabled: next })).enabled); }
    catch { setEnabled(!next); }
  };

  if (enabled === null) return null;
  return (
    <label className="settings-row settings-check" data-testid="session-credentials">
      <span className="settings-row-text">
        <strong>{t("settings.sessionCredentialTitle")}</strong>
        <small data-testid="session-credentials-hint">{t("settings.sessionCredentialHint")}</small>
      </span>
      <input type="checkbox" role="switch" aria-label={t("settings.sessionCredentialTitle")} checked={enabled} onChange={toggle} />
    </label>
  );
}
