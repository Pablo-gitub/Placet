import React, { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowRight } from "lucide-react";
import { formatWhen } from "./i18n/formatters";
import { useWide } from "./useWide";
import { MessageActions, MessageText } from "./MessageActions";

// Most urgent first: what is stuck, then what waits for a refresh, a time, an approval.
const CAUSES = ["delivery_blocked", "blocked", "awaiting_refresh", "scheduled", "awaiting_approval"];

const who = (agent, node) => (node ? `${agent} (${node})` : agent);

function Row({ item, sessions, wide, onOpenMessage, onChanged }) {
  const { t } = useTranslation();
  const { request: req } = item;
  const at = item.due_at || req.created_at;
  const summary = (
    <>
      <span className="message-id">#{req.id}</span>
      <span className="message-route">
        {who(req.sender_id, item.sender_node)} <ArrowRight size={12} aria-hidden="true" /> {who(req.recipient_id, item.recipient_node)}
      </span>
      {req.project_name && <span className="message-project">{req.project_name}</span>}
      <span className={`status-badge status-${req.status.toLowerCase()}`}>{t(`status.${req.status}`, { defaultValue: req.status })}</span>
      <time dateTime={at}>{item.due_at ? t("undelivered.due", { time: formatWhen(at) }) : formatWhen(at)}</time>
    </>
  );
  if (wide) {
    // Two columns: the text opens in the right pane.
    return (
      <div className="message-row" data-testid={`undelivered-row-${req.id}`}>
        <button type="button" className="message-row-button" onClick={() => onOpenMessage?.(req.id)}>{summary}</button>
      </div>
    );
  }
  return (
    <details className="message-row" data-testid={`undelivered-row-${req.id}`}>
      <summary>{summary}</summary>
      <div className="message-row-body">
        <MessageText req={req} />
        {item.can_cancel || item.can_forward ? <MessageActions req={req} sessions={sessions} onDone={onChanged} /> : null}
      </div>
    </details>
  );
}

/** Why the channel can not deliver to a session, how often it failed and when it looks again, with the way to try again now. */
function DeliveryBlock({ block, onRetried }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const known = block.code === "missing_user_config";
  const retry = async () => {
    setBusy(true);
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(block.session_id)}/delivery-block/retry`, { method: "POST" });
      if (res.ok) onRetried?.();
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="delivery-block" data-testid={`delivery-block-${block.session_id}`} role="note">
      <p>{t(known ? "undelivered.blockMissingConfig" : "undelivered.blockOther")}</p>
      <small className="composer-note">
        {t("undelivered.blockAttempts", { count: block.failed_attempts })} · {t("undelivered.blockChecked", { time: formatWhen(block.last_checked_at) })}
        {block.next_retry_at ? ` · ${t("undelivered.blockNext", { time: formatWhen(block.next_retry_at) })}` : ""}
      </small>
      <div className="actions-row">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={retry}>{t("undelivered.retryBlock")}</button>
      </div>
    </div>
  );
}

/** Settings → prompts to agents that have not been delivered yet, by cause, with cancel and forward. */
export default function Undelivered({ requests = [], sessions = [], onOpenMessage }) {
  const { t } = useTranslation();
  const wide = useWide();
  const [items, setItems] = useState(null);

  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((n) => n + 1), []);

  // The request feed (SSE) changes `requests`: reload when anything about them changes.
  const signature = requests.map((r) => `${r.id}:${r.status}:${r.updated_at || ""}:${r.scheduled_at || ""}`).join("|");
  useEffect(() => {
    let live = true;
    fetch("/api/requests/undelivered")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (live && Array.isArray(data)) setItems(data); })
      .catch(() => { /* the list stays as it was */ });
    return () => { live = false; };
  }, [signature, tick]);

  const groups = CAUSES.map((cause) => [cause, (items || []).filter((i) => i.cause === cause)]).filter(([, list]) => list.length);
  return (
    <section className="card settings-group" data-testid="undelivered-section">
      <h3 className="section-label">{t("undelivered.title")}</h3>
      <p className="composer-note">{t("undelivered.hint")}</p>
      {items && items.length === 0 && <p className="empty-note" data-testid="undelivered-empty">{t("undelivered.empty")}</p>}
      {groups.map(([cause, list]) => (
        <div key={cause} className="message-group" data-testid={`undelivered-group-${cause}`}>
          <h4>{t(`undelivered.cause.${cause}`)} <span className="provider-count">{list.length}</span></h4>
          {cause === "delivery_blocked" && [...new Map(list.filter((i) => i.block).map((i) => [i.block.session_id, i.block])).values()]
            .map((block) => <DeliveryBlock key={block.session_id} block={block} onRetried={reload} />)}
          {list.map((item) => (
            <Row key={item.request.id} item={item} sessions={sessions} wide={wide}
              onOpenMessage={onOpenMessage} onChanged={reload} />
          ))}
        </div>
      ))}
    </section>
  );
}
