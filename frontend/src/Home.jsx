import React, { useState } from "react";
import { CheckCircle, Circle, MessageCircleQuestion, Plus } from "lucide-react";
import RequestCard from "./RequestCard";
import AddSessionsDialog from "./AddSessionsDialog";
import {
  activityOf,
  byActivity,
  providerOf,
  sessionName,
  sessionPlace,
  sessionStatus,
  timeAgo,
} from "./sessionInfo";

function SessionRow({ session, questions, onOpen }) {
  const status = sessionStatus(session);
  return (
    <button
      type="button"
      className="card session-row-card"
      data-testid={`session-row-${session.id}`}
      onClick={() => onOpen(session.id)}
    >
      <div className="session-row">
        <span className="provider-chip">{providerOf(session).label}</span>
        <strong className="session-name">{sessionName(session)}</strong>
        {questions > 0 && (
          <span className="badge-count" title="Domande in attesa" data-testid={`session-questions-${session.id}`}>
            {questions}
          </span>
        )}
      </div>
      <div className="session-meta">
        <span>{sessionPlace(session)}</span>
        <span className={`session-state state-${status.key}`}>
          <Circle size={8} fill="currentColor" />
          {status.label} · {timeAgo(activityOf(session))}
        </span>
      </div>
    </button>
  );
}

/** Questions waiting for the user, then the sessions they follow (RF-40, RF-41). */
export default function Home({
  requests,
  sessions,
  onDecide,
  submittingId,
  focusId,
  onOpenSession,
  onTogglePin,
}) {
  const [adding, setAdding] = useState(false);
  const pending = requests.filter((r) => r.status === "PENDING");
  const pinned = sessions.filter((s) => s.pinned).sort(byActivity);
  const questionsOf = (id) => pending.filter((r) => r.sender_session_id === id).length;

  return (
    <div data-testid="home-section">
      <section className="home-section">
        <h2 className="home-title">
          <MessageCircleQuestion size={16} /> Domande
          {pending.length > 0 && <span className="badge-count">{pending.length}</span>}
        </h2>
        {pending.length === 0 ? (
          <div className="empty-hint" data-testid="empty-inbox">
            <CheckCircle size={16} /> Nessuna domanda in attesa.
          </div>
        ) : (
          pending.map((req) => (
            <RequestCard
              key={req.id}
              req={req}
              submitting={submittingId === req.id}
              highlighted={req.id === focusId}
              onDecide={onDecide}
              sessions={sessions}
              onOpenSession={onOpenSession}
            />
          ))
        )}
      </section>

      <section className="home-section">
        <h2 className="home-title">Le tue sessioni</h2>
        {pinned.length === 0 && (
          <div className="empty-hint" data-testid="empty-sessions">
            Aggiungi le sessioni che vuoi seguire da qui.
          </div>
        )}
        {pinned.map((s) => (
          <SessionRow key={s.id} session={s} questions={questionsOf(s.id)} onOpen={onOpenSession} />
        ))}
        <button
          type="button"
          className="btn btn-secondary add-sessions"
          data-testid="open-add-sessions"
          onClick={() => setAdding(true)}
        >
          <Plus size={16} /> Aggiungi / vedi tutte
        </button>
      </section>

      {adding && (
        <AddSessionsDialog
          sessions={sessions}
          onToggle={onTogglePin}
          onOpen={(id) => {
            setAdding(false);
            onOpenSession(id);
          }}
          onClose={() => setAdding(false)}
        />
      )}
    </div>
  );
}
