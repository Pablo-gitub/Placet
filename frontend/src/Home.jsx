import { useTranslation } from "react-i18next";
import React, { useState } from "react";
import { CheckCircle, ChevronDown, ChevronRight, Circle, MessageCircleQuestion, PauseCircle, Plus } from "lucide-react";
import RequestCard from "./RequestCard";
import { needsReturnApproval } from "./decisions";
import DesktopCapacity from "./DesktopCapacity";
import McpWarning from "./McpWarning";
import { queueStuck } from "./aspects";
import useHoldClock from "./useHoldClock";
import AddSessionsDialog from "./AddSessionsDialog";
import UsageBar from "./UsageBar";
import AutomationBar from "./AutomationBar";
import {
  activityOf,
  byActivity,
  providerClass,
  providerOf,
  sessionLabels,
  sessionName,
  sessionPlace,
  sessionStatus,
  timeAgo,
} from "./sessionInfo";

function SessionRow({ session, label, questions, stuck, onOpen, active }) {
  const { t } = useTranslation();
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
        <strong className="session-name">{label || sessionName(session)}</strong>
        <McpWarning session={session} compact />
        {stuck && (
          <span className="badge-held" title={t("home.queueHeld")} data-testid={`session-held-${session.id}`}>
            <PauseCircle size={14} aria-label={t("home.queueHeld")} />
          </span>
        )}
        {questions > 0 && (
          <span className="badge-count" title={t("home.pendingQuestions", { count: questions })} data-testid={`session-questions-${session.id}`}>
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
function ProviderGroup({ provider, sessions, labels, questionsOf, stuckOf, collapsed, onToggle, onOpen, activeId }) {
  const { t } = useTranslation();
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
          {busy > 0 && ` · ${t("home.busy", { count: busy })}`}
        </span>
        {questions > 0 && <span className="badge-count">{questions}</span>}
      </button>
      {provider.key === "claude-code" && sessions.some((s) => s.entrypoint === "claude-desktop") && <DesktopCapacity />}
      {!collapsed &&
        sessions.map((s) => (
          <SessionRow
            key={s.id}
            session={s}
            label={labels?.[s.id]}
            questions={questionsOf(s.id)}
            stuck={stuckOf(s.id)}
            onOpen={onOpen}
            active={s.id === activeId}
          />
        ))}
    </div>
  );
}

/** Questions waiting for the user, then the sessions they follow (RF-40, RF-41). */
export default function Home({
  requests,
  sessions,
  usage,
  automationVersion,
  onDecide,
  submittingId,
  focusId,
  onOpenSession,
  onTogglePin,
  activeSessionId,
}) {
  const { t } = useTranslation();
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
  const pending = requests.filter((r) => r.status === "PENDING" || needsReturnApproval(r));
  const pinned = sessions.filter((s) => s.pinned).sort(byActivity);
  const labels = sessionLabels(pinned);
  const questionsOf = (id) => pending.filter((r) => r.sender_session_id === id).length;
  const holdNow = useHoldClock(requests, null);
  const stuckOf = (id) => queueStuck(requests, id, holdNow);
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
      <UsageBar usage={usage} />
      <AutomationBar version={automationVersion} />
      <section className="home-section">
        <h2 className="home-title">
          <MessageCircleQuestion size={16} /> {t("home.questions")}
          {pending.length > 0 && <span className="badge-count">{pending.length}</span>}
        </h2>
        {pending.length === 0 ? (
          <div className="empty-hint" data-testid="empty-inbox">
            <CheckCircle size={16} /> {t("home.emptyQuestions")}
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
        <h2 className="home-title">{t("home.sessions")}</h2>
        {pinned.length === 0 && (
          <div className="empty-hint" data-testid="empty-sessions">
            {t("home.emptySessions")}
          </div>
        )}
        {groups.map((g) => (
          <ProviderGroup
            key={g.provider.key}
            provider={g.provider}
            sessions={g.sessions}
            labels={labels}
            questionsOf={questionsOf}
            stuckOf={stuckOf}
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
          <Plus size={16} /> {t("home.addSessions")}
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
