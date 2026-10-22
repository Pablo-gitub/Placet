import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { apiErrorDescriptor } from "./i18n/errors";
import { timeAgo } from "./sessionInfo";

/**
 * A Claude Desktop session asked to open: what Placet knows, honestly. The launcher accepting
 * the link is not the session starting; after the window with no sign of it, the message is
 * still in Placet and the user can write in the chat on the PC or ask again.
 */
export default function WakeNotice({ session }) {
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const state = session?.wake_state;
  if (!session || session.reachable || session.entrypoint !== "claude-desktop") return null;
  if (!["open_requested", "waiting_for_session", "wake_unconfirmed", "wake_failed"].includes(state)) return null;

  const retry = async () => {
    setPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(session.id)}/wake`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setError(apiErrorDescriptor(data, res.status));
      }
    } catch {
      setError({ key: "errors.operation", params: { status: 0 } });
    } finally {
      setPending(false);
    }
  };

  if (state === "open_requested" || state === "waiting_for_session") {
    return <p className="composer-note" role="status" data-testid="wake-notice">{t("wake.waiting")}</p>;
  }
  return (
    <div className="wake-notice" role="status" data-testid="wake-notice">
      <p>{t(state === "wake_failed" ? "wake.failed" : "wake.unconfirmed")}</p>
      {session.wake_at && <small>{t("wake.lastAttempt", { when: timeAgo(session.wake_at) })}</small>}
      <button type="button" data-testid="wake-retry" disabled={pending} onClick={retry}>{t("wake.retry")}</button>
      {error && <p className="composer-note" role="alert" data-testid="wake-error">{t(error.key, error.params)}</p>}
    </div>
  );
}
