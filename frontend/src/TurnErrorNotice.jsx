import React from "react";
import { useTranslation } from "react-i18next";
import { timeAgo } from "./sessionInfo";

/**
 * The last turn ended on an API error (Claude Code StopFailure): the session will not go on by
 * itself, and messages for it wait until the user writes to it in the app.
 */
export default function TurnErrorNotice({ session }) {
  const { t } = useTranslation();
  if (!session?.turn_error || session.state === "busy") return null;
  return (
    <div className="wake-notice" role="status" data-testid="turn-error">
      <p>{session.turn_error === "rate_limit" ? t("turnError.rateLimit") : t("turnError.other", { error: session.turn_error })}</p>
      {session.turn_error_at && <small>{timeAgo(session.turn_error_at)}</small>}
    </div>
  );
}
