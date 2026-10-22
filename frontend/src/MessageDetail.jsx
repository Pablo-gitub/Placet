import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";
import MessageBody from "./MessageBody";
import { MessageActions, MessageText } from "./MessageActions";
import { isUndelivered } from "./messages";
import { formatDateTime } from "./i18n/formatters";

/** One message in full, in the right pane (or as a page on the phone): text, reply, timeline and, if it is still undelivered, its actions. */
export default function MessageDetail({ requestId, requests = [], sessions = [], onBack, onOpenSession }) {
  const { t } = useTranslation();
  const known = requests.find((r) => r.id === requestId);
  const [fetched, setFetched] = useState(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (known) return undefined;
    let live = true;
    fetch(`/api/requests/${requestId}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (live) { setFetched(data); setMissing(!data); } })
      .catch(() => { if (live) setMissing(true); });
    return () => { live = false; };
  }, [requestId, known]);

  const req = known || fetched;
  return (
    <div className="card message-detail" data-testid="message-detail">
      <button type="button" className="link-button project-detail-back-btn" data-testid="message-back" onClick={onBack}>
        <ArrowLeft size={16} /> {t("undelivered.back")}
      </button>
      <h2>{t("undelivered.title_detail", { id: requestId })}</h2>
      {!req && missing && <p data-testid="message-missing">{t("errors.notFound")}</p>}
      {req && (
        <>
          <p className="composer-note">
            {req.sender_id} → {req.recipient_id} · {formatDateTime(req.created_at)}
          </p>
          <section className="request-section">
            <MessageText req={req} />
          </section>
          {isUndelivered(req) && <MessageActions req={req} sessions={sessions} />}
          <MessageBody req={req} sessions={sessions} onOpenSession={onOpenSession} />
        </>
      )}
    </div>
  );
}
