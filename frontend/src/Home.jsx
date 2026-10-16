import React, { useState } from "react";
import { CheckCircle, ChevronDown, ChevronRight, Circle, MessageCircleQuestion, Plus } from "lucide-react";
import RequestCard from "./RequestCard";
import AddSessionsDialog from "./AddSessionsDialog";
import {
  activityOf,
  byActivity,
  providerClass,
  providerOf,
  sessionName,
  sessionPlace,
  sessionStatus,
  timeAgo,
} from "./sessionInfo";

function SessionRow({ session, questions, onOpen, active }) {
  const status = sessionStatus(session);
  return (
    <button
      type="button"
      className={`card session-row-card ${providerClass(session)}${active ? " active" : ""}`}
      aria-current={active ? "page" : undefined}
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

const COLLAPSED_KEY = "placet.collapsedProviders";

function loadCollapsed() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

/**
 * The followed sessions of one provider, under a header that opens and
 * closes; providers come in order of their latest activity.
 */
function ProviderGroup({ provider, sessions, questionsOf, collapsed, onToggle, onOpen, activeId }) {
  const busy = sessions.filter((s) => sessionStatus(s).key === "busy").length;
  const questions = sessions.reduce((n, s) => n + questionsOf(s.id), 0);
  return (
    <div className={`provider-group prov-${provider.key}`} data-testid={`provider-group-${provider.key}`}>
      <button
        type="button"
        className="provider-group-header"
        aria-expanded={!collapsed}
        data-testid={`provider-toggle-${provider.key}`}
        onClick={() => onToggle(provider.key)}
      >
        {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
        <span className="provider-dot" />
        <strong>{provider.label}</strong>
        <span className="provider-group-count">
          {sessions.length}
          {busy > 0 && ` · ${busy} al lavoro`}
        </span>
        {questions > 0 && <span className="badge-count">{questions}</span>}
      </button>
      {!collapsed &&
        sessions.map((s) => (
          <SessionRow key={s.id} session={s} questions={questionsOf(s.id)} onOpen={onOpen} active={s.id === activeId} />
        ))}
    </div>
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
  activeSessionId,
}) {
  const [adding, setAdding] = useState(false);
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const toggleProvider = (key) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      try {
        localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...next]));
      } catch {
        // private mode: the choice lasts until the page is reloaded
      }
      return next;
    });
  const pending = requests.filter((r) => r.status === "PENDING");
  const pinned = sessions.filter((s) => s.pinned).sort(byActivity);
  const questionsOf = (id) => pending.filter((r) => r.sender_session_id === id).length;
  // pinned is sorted by activity: a provider's first session is its latest.
  const groups = [];
  for (const s of pinned) {
    const provider = providerOf(s);
    let group = groups.find((g) => g.provider.key === provider.key);
    if (!group) groups.push((group = { provider, sessions: [] }));
    group.sessions.push(s);
  }

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
        {groups.map((g) => (
          <ProviderGroup
            key={g.provider.key}
            provider={g.provider}
            sessions={g.sessions}
            questionsOf={questionsOf}
            collapsed={collapsed.has(g.provider.key)}
            onToggle={toggleProvider}
            onOpen={onOpenSession}
            activeId={activeSessionId}
          />
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
