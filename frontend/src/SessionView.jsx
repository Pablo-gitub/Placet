import React, { useLayoutEffect, useRef, useState } from "react";
import Composer from "./Composer";
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
  const Icon = ACTION_ICONS[entry.kind] || Wrench;
  return (
    <li className="action-line">
      <Icon size={13} />
      <span>{actionLabel(entry)}</span>
    </li>
  );
}

function ActionGroup({ entries }) {
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
        {entries.length} azioni
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
  const [open, setOpen] = useState(false);
  const long = entry.text.length > LONG_PLACET_CHARS;
  return (
    <div className="msg msg-placet" data-testid="chat-placet">
      <span className="placet-tag">Placet</span>
      <div className={`placet-text${long && !open ? " clamped" : ""}`}>{entry.text}</div>
      {long && (
        <button type="button" className="link-button" onClick={() => setOpen((v) => !v)}>
          {open ? "Mostra meno" : "Mostra tutto"}
        </button>
      )}
    </div>
  );
}

function ChatEntry({ entry }) {
  switch (entry.role) {
    case "user":
      return (
        <div className="msg msg-user" data-testid="chat-user">
          <div className="bubble">{entry.text}</div>
          <time>
            {timeOf(entry.at)}
            {entry.via === "placet" && " · dal telefono"}
          </time>
        </div>
      );
    case "assistant":
      return (
        <div className="msg msg-assistant" data-testid="chat-assistant">
          <Markdown>{entry.text}</Markdown>
          <time>{timeOf(entry.at)}</time>
        </div>
      );
    case "placet":
      return <PlacetMessage entry={entry} />;
    case "command":
      return (
        <div className="msg msg-command" data-testid="chat-command">
          <code>{entry.text}</code>
        </div>
      );
    case "compact":
      return (
        <div className="chat-divider" data-testid="chat-compact">
          Conversazione compattata
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
  APPROVED: "In attesa che la sessione sia libera",
  DELIVERING: "In consegna…",
  DELIVERY_FAILED: "Non consegnato",
  EXPIRED: "Non consegnato: scaduto",
};

function Outgoing({ req, onDelivery }) {
  const uncertain = isUncertain(req);
  const failed = req.status === "DELIVERY_FAILED" || req.status === "EXPIRED";
  return (
    <div
      className={`msg msg-user outgoing${failed ? " failed" : ""}${uncertain ? " uncertain" : ""}`}
      data-testid={`outgoing-${req.id}`}
    >
      <div className="bubble">{req.prompt}</div>
      <time>
        {uncertain ? "Consegna incerta: forse è già arrivato, ma non c'è conferma" : OUTGOING_STATES[req.status]}
      </time>
      {onDelivery && (uncertain || req.status === "APPROVED") && (
        <div className="outgoing-actions">
          {uncertain && (
            <button type="button" data-testid={`retry-${req.id}`} onClick={() => onDelivery(req.id, "retry")}>
              Riprova
            </button>
          )}
          <button type="button" data-testid={`cancel-${req.id}`} onClick={() => onDelivery(req.id, "cancel")}>
            Annulla
          </button>
        </div>
      )}
    </div>
  );
}

function QueueHold({ req, waiting, onDelivery }) {
  const cancel = () => {
    if (window.confirm("L'agente potrebbe continuare a lavorarci: Placet smette solo di aspettarlo. Annullare?")) {
      onDelivery(req.id, "cancel");
    }
  };
  return (
    <div className="queue-hold" data-testid={`queue-hold-${req.id}`}>
      <p>
        {req.execution === "started"
          ? "Il turno è iniziato ma Placet non ne vede la fine."
          : "Consegnato. Placet non riesce a verificare l'esecuzione."}{" "}
        {waiting > 0 ? "I messaggi successivi restano in attesa." : "I prossimi messaggi aspetteranno."}
      </p>
      <div className="outgoing-actions">
        <button type="button" data-testid={`finished-${req.id}`} onClick={() => onDelivery(req.id, "finished")}>
          Confermo che è terminata
        </button>
        {waiting > 0 && (
          <button type="button" data-testid={`queue-next-${req.id}`} onClick={() => onDelivery(req.id, "queue_next")}>
            Accoda comunque il prossimo
          </button>
        )}
        {req.status !== "COMPLETED" && (
          <button type="button" data-testid={`cancel-${req.id}`} onClick={cancel}>
            Annulla
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
  onBack,
  onDecide,
  onSend,
  onDelivery,
  submittingId,
  focusId,
}) {
  const refreshKey = session ? `${session.state}|${session.activity_at}` : undefined;
  const { entries, status, error, hasOlder, loadingOlder, loadOlder, retry } = useTranscript(
    sessionId,
    refreshKey
  );
  const scrollRef = useRef(null);
  const following = useRef(true);
  const anchor = useRef(null);
  const questions = requests.filter((r) => r.status === "PENDING" && r.sender_session_id === sessionId);
  const outgoing = requests
    .filter((r) => r.question_type === "message" && r.recipient_session_id === sessionId && r.status in OUTGOING_STATES)
    .sort((a, b) => a.id - b.id);
  const held = requests.filter((r) => r.recipient_session_id === sessionId && holdsQueueUnseen(r));

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
        <button type="button" className="icon-button" aria-label="Indietro" onClick={onBack}>
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
        {status === "loading" && <p className="chat-status">Carico la conversazione…</p>}
        {status === "error" && (
          <div className="chat-error" data-testid="chat-error">
            <p>{error}</p>
            <button type="button" className="btn btn-secondary" onClick={retry}>
              <RefreshCw size={14} /> Riprova
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
                {loadingOlder ? "Carico…" : "Messaggi precedenti"}
              </button>
            ) : (
              <p className="chat-status">Inizio della conversazione</p>
            )}
            {entries.length === 0 && <p className="chat-status">Nessun messaggio.</p>}
            {groupEntries(entries).map((item) =>
              item.type === "actions" ? (
                <ActionGroup key={item.key} entries={item.entries} />
              ) : (
                <ChatEntry key={item.key} entry={item.entry} />
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
          <Outgoing key={req.id} req={req} onDelivery={onDelivery} />
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
          session={session}
          onSend={async (text) => {
            following.current = true;
            return onSend(sessionId, text);
          }}
        />
      )}
    </div>
  );
}
