import { useTranslation } from "react-i18next";
import { needsReturnApproval } from "./decisions";
import React, { useLayoutEffect, useRef, useState } from "react";
import Attachments from "./Attachments";
import Composer from "./Composer";
import { limitReset, resetClock } from "./usage";
import { splitPrompt } from "./draft";
import {
  ArrowLeft,
  Bot,
  ChevronDown,
  ChevronRight,
  Circle,
  FileText,
  Globe,
  Pencil,
  Plug,
  RefreshCw,
  Search,
  Sparkles,
  Terminal,
  Wrench,
} from "lucide-react";
import Markdown from "./Markdown";
import RequestCard from "./RequestCard";
import { actionLabel, groupEntries, timeOf } from "./chatEntries";
import { providerClass, providerOf, sessionName, sessionPlace, sessionStatus } from "./sessionInfo";
import { useTranscript } from "./useTranscript";
import { holdsQueueUnseen, isUncertain } from "./aspects";

const ACTION_ICONS = {
  command: Terminal,
  read: FileText,
  edit: Pencil,
  write: Pencil,
  search: Search,
  web: Globe,
  mcp: Plug,
  skill: Sparkles,
  agent: Bot,
};
// How close to the top (px) scrolling loads older messages, and how close to
// the bottom counts as "following" the conversation.
const LOAD_OLDER_PX = 150;
const FOLLOW_PX = 120;
const LONG_PLACET_CHARS = 400;

function ActionLine({ entry }) {
  useTranslation();
  const Icon = ACTION_ICONS[entry.kind] || Wrench;
  return (
    <li className="action-line">
      <Icon size={13} />
      <span>{actionLabel(entry)}</span>
    </li>
  );
}

function ActionGroup({ entries }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (entries.length <= 2) {
    return (
      <ul className="chat-actions" data-testid="chat-actions">
        {entries.map((e) => (
          <ActionLine key={e.cursor} entry={e} />
        ))}
      </ul>
    );
  }
  return (
    <div className="chat-actions" data-testid="chat-actions">
      <button type="button" className="actions-toggle" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        {t("chat.actions", { count: entries.length })}
        {!open && <span className="actions-last"> · {actionLabel(entries[entries.length - 1])}</span>}
      </button>
      {open && (
        <ul>
          {entries.map((e) => (
            <ActionLine key={e.cursor} entry={e} />
          ))}
        </ul>
      )}
    </div>
  );
}

function PlacetMessage({ entry }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const long = entry.text.length > LONG_PLACET_CHARS;
  return (
    <div className="msg msg-placet" data-testid="chat-placet">
      <span className="placet-tag">Placet</span>
      <div className={`placet-text${long && !open ? " clamped" : ""}`}>{entry.text}</div>
      {long && (
        <button type="button" className="link-button" onClick={() => setOpen((v) => !v)}>
          {t(open ? "chat.showLess" : "chat.showAll")}
        </button>
      )}
    </div>
  );
}

function ChatEntry({ entry, sessionId }) {
  const { t } = useTranslation();
  const images = <Attachments sessionId={sessionId} items={entry.attachments} />;
  switch (entry.role) {
    case "user":
      return (
        <div className="msg msg-user" data-testid="chat-user">
          {entry.text && <div className="bubble">{entry.text}</div>}
          {images}
          <time>
            {timeOf(entry.at)}
            {entry.via === "placet" && t("chat.phone")}
          </time>
        </div>
      );
    case "assistant":
      return (
        <div className="msg msg-assistant" data-testid="chat-assistant">
          {entry.text && <Markdown>{entry.text}</Markdown>}
          {images}
          <time>{timeOf(entry.at)}</time>
        </div>
      );
    case "placet":
      return <PlacetMessage entry={entry} />;
    case "media":
      // An image an agent's tool produced (e.g. Antigravity's generate_image).
      return (
        <div className="msg msg-media" data-testid="chat-media">
          <span className="media-label">{entry.label_key === "shared_file" ? t("chat.sharedFile") : entry.label_key === "generated_image" ? t("chat.generatedImage") : entry.text}</span>
          {images}
          <time>{timeOf(entry.at)}</time>
        </div>
      );
    case "command":
      return (
        <div className="msg msg-command" data-testid="chat-command">
          <code>{entry.text}</code>
        </div>
      );
    case "compact":
      return (
        <div className="chat-divider" data-testid="chat-compact">
          {t("chat.compacted")}
        </div>
      );
    case "notice":
      return (
        <div className="msg msg-notice" data-testid="chat-notice">
          {entry.text}
        </div>
      );
    default:
      return null;
  }
}

/** A session's conversation with its open questions at the bottom (RF-30, RF-33). */
// The user's messages not yet in the conversation: waiting, or failed.
const OUTGOING_STATES = {
  APPROVED: "deliveryUi.waiting",
  NOW: "deliveryUi.now",
  DELIVERING: "deliveryUi.delivering",
  DELIVERY_FAILED: "deliveryUi.failed",
  EXECUTING: "deliveryUi.accepted",
  EXPIRED: "deliveryUi.expired",
};

// Delivered, but its turn not seen yet: still shown as the user's message.
const notStarted = (req) => req.status !== "EXECUTING" || req.execution === "unobserved";

function Outgoing({ req, session, limitAt, onDelivery, onEdit }) {
  const { t } = useTranslation();
  const uncertain = isUncertain(req);
  const failed = req.status === "DELIVERY_FAILED" || req.status === "EXPIRED";
  return (
    <div
      className={`msg msg-user outgoing${failed ? " failed" : ""}${uncertain ? " uncertain" : ""}`}
      data-testid={`outgoing-${req.id}`}
    >
      <div className="bubble">{req.prompt}</div>
      <time>
        {uncertain
          ? t("deliveryUi.uncertain")
          : req.delivery_mode === "now" && ["APPROVED", "DELIVERING"].includes(req.status)
            ? t(OUTGOING_STATES.NOW)
            : req.status === "APPROVED" && limitAt
              ? t("deliveryUi.limited", { provider: providerOf(session).label, time: resetClock(limitAt) })
            : req.status === "APPROVED" && session?.suspended_at
              ? session.provider === "claude-code"
                ? t("deliveryUi.suspendedClaude")
                : t("deliveryUi.suspendedCodex")
              : req.status === "EXECUTING" && session?.provider === "codex"
                ? t("deliveryUi.queuedCodex")
                : t(OUTGOING_STATES[req.status])}
      </time>
      {onDelivery && (uncertain || req.status === "APPROVED") && (
        <div className="outgoing-actions">
          {uncertain && (
            <button type="button" data-testid={`retry-${req.id}`} onClick={() => onDelivery(req.id, "retry")}>
              {t("deliveryUi.retry")}
            </button>
          )}
          {req.status === "APPROVED" && onEdit && (
            <button type="button" data-testid={`edit-${req.id}`} onClick={() => onEdit(req)}>
              {t("deliveryUi.edit")}
            </button>
          )}
          <button type="button" data-testid={`cancel-${req.id}`} onClick={() => onDelivery(req.id, "cancel")}>
            {t("deliveryUi.cancel")}
          </button>
        </div>
      )}
    </div>
  );
}

function QueueHold({ req, waiting, onDelivery }) {
  const { t } = useTranslation();
  const cancel = () => {
    if (window.confirm(t("deliveryUi.cancelConfirm"))) {
      onDelivery(req.id, "cancel");
    }
  };
  return (
    <div className="queue-hold" data-testid={`queue-hold-${req.id}`}>
      <p>
        {req.execution === "started"
          ? t("deliveryUi.startedUnseen")
          : t("deliveryUi.acceptedUnseen")}{" "}
        {waiting > 0 ? t("deliveryUi.waitingNext") : t("deliveryUi.willWait")}
      </p>
      <div className="outgoing-actions">
        <button type="button" data-testid={`finished-${req.id}`} onClick={() => onDelivery(req.id, "finished")}>
          {t("deliveryUi.finished")}
        </button>
        {waiting > 0 && (
          <button type="button" data-testid={`queue-next-${req.id}`} onClick={() => onDelivery(req.id, "queue_next")}>
            {t("deliveryUi.queueNext")}
          </button>
        )}
        {req.status !== "COMPLETED" && (
          <button type="button" data-testid={`cancel-${req.id}`} onClick={cancel}>
            {t("deliveryUi.cancel")}
          </button>
        )}
      </div>
    </div>
  );
}

export default function SessionView({
  sessionId,
  session,
  requests,
  sessions,
  usage,
  onBack,
  onDecide,
  onSend,
  onDelivery,
  submittingId,
  focusId,
}) {
  const { t } = useTranslation();
  // RF-60: a provider at its usage limit gets nothing until the reset.
  const limitAt = session ? limitReset(usage, session.provider) : null;
  const refreshKey = session ? `${session.state}|${session.activity_at}` : undefined;
  const { entries, status, error, hasOlder, loadingOlder, loadOlder, retry } = useTranscript(
    sessionId,
    refreshKey
  );
  const scrollRef = useRef(null);
  const following = useRef(true);
  const anchor = useRef(null);
  const questions = requests.filter((r) => (r.status === "PENDING" || needsReturnApproval(r)) && r.sender_session_id === sessionId);
  const outgoing = requests
    .filter((r) => r.question_type === "message" && r.recipient_session_id === sessionId
      && r.status in OUTGOING_STATES && notStarted(r))
    .sort((a, b) => a.id - b.id);
  const held = requests.filter((r) => r.recipient_session_id === sessionId && holdsQueueUnseen(r));
  // Editing withdraws a waiting message and puts it back in the composer.
  const [draft, setDraft] = useState(null);
  const edit = async (req) => {
    if (await onDelivery(req.id, "cancel")) setDraft({ ...splitPrompt(req.prompt, sessions), nonce: req.id });
  };

  // Keep the view where the reader is: at the bottom while following, on the
  // same message after older ones are added above.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (anchor.current) {
      el.scrollTop = el.scrollHeight - anchor.current.height + anchor.current.top;
      anchor.current = null;
    } else if (following.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [entries, questions.length, outgoing.length]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    following.current = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_PX;
    if (el.scrollTop < LOAD_OLDER_PX && hasOlder && !loadingOlder) {
      anchor.current = { height: el.scrollHeight, top: el.scrollTop };
      loadOlder().then((loaded) => {
        if (!loaded) anchor.current = null;
      });
    }
  };

  const provider = session ? providerOf(session) : null;
  const state = session ? sessionStatus(session) : null;

  return (
    <div className={`session-view${session ? ` ${providerClass(session)}` : ""}`} data-testid="session-view">
      <div className="session-view-header">
        <button type="button" className="icon-button" aria-label={t("chat.back")} onClick={onBack}>
          <ArrowLeft size={18} />
        </button>
        <div className="session-view-title">
          <strong data-testid="session-view-name">{session ? sessionName(session) : sessionId.slice(0, 8)}</strong>
          {session && (
            <span className="session-meta-line">
              {provider.label}
              {sessionPlace(session) && ` · ${sessionPlace(session)}`}
            </span>
          )}
        </div>
        {state && (
          <span className={`session-state state-${state.key}`} data-testid="session-view-state">
            <Circle size={8} fill="currentColor" />
            {state.label}
          </span>
        )}
      </div>

      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll} data-testid="chat-scroll">
        {status === "loading" && <p className="chat-status">{t("chat.loading")}</p>}
        {status === "error" && (
          <div className="chat-error" data-testid="chat-error">
            <p>{error && t(error.key, error.params)}</p>
            <button type="button" className="btn btn-secondary" onClick={retry}>
              <RefreshCw size={14} /> {t("chat.retry")}
            </button>
          </div>
        )}
        {status === "ready" && (
          <>
            {hasOlder ? (
              <button
                type="button"
                className="link-button load-older"
                data-testid="load-older"
                disabled={loadingOlder}
                onClick={() => loadOlder()}
              >
                {t(loadingOlder ? "chat.loadingOlder" : "chat.older")}
              </button>
            ) : (
              <p className="chat-status">{t("chat.start")}</p>
            )}
            {error && (
              <div className="chat-error" role="alert" data-testid="older-error">
                <p>{t(error.key, error.params)}</p>
                <button type="button" className="btn btn-secondary" disabled={loadingOlder} onClick={() => loadOlder()}>
                  {t("chat.retry")}
                </button>
              </div>
            )}
            {entries.length === 0 && <p className="chat-status">{t("chat.empty")}</p>}
            {groupEntries(entries).map((item) =>
              item.type === "actions" ? (
                <ActionGroup key={item.key} entries={item.entries} />
              ) : (
                <ChatEntry key={item.key} entry={item.entry} sessionId={sessionId} />
              )
            )}
          </>
        )}
        {onDelivery &&
          held.map((req) => (
            <QueueHold
              key={`hold-${req.id}`}
              req={req}
              waiting={outgoing.filter((o) => o.status === "APPROVED").length}
              onDelivery={onDelivery}
            />
          ))}
        {outgoing.map((req) => (
          <Outgoing key={req.id} req={req} session={session} limitAt={limitAt} onDelivery={onDelivery} onEdit={onDelivery && edit} />
        ))}
        {questions.map((req) => (
          <RequestCard
            key={req.id}
            req={req}
            submitting={submittingId === req.id}
            highlighted={req.id === focusId}
            onDecide={onDecide}
            sessions={sessions}
          />
        ))}
      </div>
      {onSend && (
        <Composer
          key={`${sessionId}:${draft?.nonce ?? ""}`}
          session={session}
          limitAt={limitAt}
          draft={draft}
          sessions={sessions}
          onSend={async (text, mentions, attachments, mode) => {
            following.current = true;
            if (mode === "now") return onSend(sessionId, text, mentions || [], attachments || [], "now");
            return attachments?.length ? onSend(sessionId, text, mentions, attachments)
              : mentions?.length ? onSend(sessionId, text, mentions) : onSend(sessionId, text);
          }}
        />
      )}
    </div>
  );
}
