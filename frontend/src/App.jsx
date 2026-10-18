import { useTranslation } from "react-i18next";
import LanguageSelect from "./i18n/LanguageSelect";
import { apiErrorDescriptor } from "./i18n/errors";
import { needsReturnApproval } from "./decisions";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle, Home as HomeIcon, MessagesSquare, Radio, ShieldCheck } from "lucide-react";
import Home from "./Home";
import History from "./History";
import SessionView from "./SessionView";
import { requestIdFromHash, routeFromHash } from "./decisions";
import { useWide } from "./useWide";

function upsert(list, item) {
  const index = list.findIndex((x) => x.id === item.id);
  if (index < 0) return [item, ...list];
  const next = [...list];
  next[index] = item;
  return next;
}

/** Where a notification link to a request leads: its session, or home / history. */
function targetOf(req) {
  if (needsReturnApproval(req)) return "#/";
  if (req.status === "PENDING") return req.sender_session_id ? `#/sessione/${req.sender_session_id}` : "#/";
  return "#/storico";
}

export default function App() {
  const { t } = useTranslation();
  const [requests, setRequests] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [route, setRoute] = useState(() => routeFromHash(window.location.hash));
  const [focusId, setFocusId] = useState(() => requestIdFromHash(window.location.hash));
  const [connected, setConnected] = useState(false);
  const [submittingId, setSubmittingId] = useState(null);
  const [feedback, setFeedback] = useState(null);
  const wide = useWide();
  // On the phone a conversation takes the whole screen (and follows the keyboard).
  const inSession = route.view === "session" && !wide;

  useEffect(() => {
    if (!inSession) return;
    // iOS reduces the visual viewport for the keyboard without necessarily
    // resizing the layout viewport (including dvh). Follow both its size and
    // offset so Safari's focus scrolling cannot hide the back button.
    const viewport = window.visualViewport;
    const updateViewport = () => {
      document.documentElement.style.setProperty("--chat-height", `${viewport?.height ?? window.innerHeight}px`);
      document.documentElement.style.setProperty("--chat-top", `${viewport?.offsetTop ?? 0}px`);
    };
    document.body.classList.add("chat-open");
    updateViewport();
    viewport?.addEventListener("resize", updateViewport);
    viewport?.addEventListener("scroll", updateViewport);
    window.addEventListener("resize", updateViewport);
    return () => {
      document.body.classList.remove("chat-open");
      document.documentElement.style.removeProperty("--chat-height");
      document.documentElement.style.removeProperty("--chat-top");
      viewport?.removeEventListener("resize", updateViewport);
      viewport?.removeEventListener("scroll", updateViewport);
      window.removeEventListener("resize", updateViewport);
    };
  }, [inSession]);
  // A notification link (#request-N) waits here until its request is loaded;
  // then the view moves there and scrolls to it.
  const jump = useRef(requestIdFromHash(window.location.hash));
  const scrollTo = useRef(null);
  // The latest requests, for the hashchange listener registered once.
  const requestsRef = useRef(requests);
  useEffect(() => {
    requestsRef.current = requests;
  }, [requests]);

  const navigate = (hash) => {
    if (window.location.hash === hash) setRoute(routeFromHash(hash));
    else window.location.hash = hash;
  };

  const resolveJump = useCallback((list) => {
    const req = jump.current !== null && list.find((r) => r.id === jump.current);
    if (!req) return;
    jump.current = null;
    scrollTo.current = req.id;
    const target = targetOf(req);
    if (window.location.hash === target) setRoute(routeFromHash(target));
    else window.location.hash = target;
  }, []);

  const sessionsRef = useRef(sessions);
  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  const fetchRequests = useCallback(async () => {
    try {
      const res = await fetch("/api/requests");
      if (res.ok) setRequests(await res.json());
    } catch (e) {
      console.error("Error fetching requests:", e);
    }
  }, []);

  const fetchSessions = useCallback(async () => {
    try {
      // Closed sessions too: one the user follows stays in the home, marked closed.
      const res = await fetch("/api/sessions?include_ended=true");
      if (res.ok) setSessions(await res.json());
    } catch (e) {
      console.error("Error fetching sessions:", e);
    }
  }, []);

  useEffect(() => {
    // Data fetching: the state updates happen after the response arrives.
    // oxlint-disable-next-line react/set-state-in-effect
    fetchRequests();
    fetchSessions();

    let evtSource = null;
    try {
      evtSource = new EventSource("/api/events");
      evtSource.onopen = () => {
        setConnected(true);
        fetchRequests();
        fetchSessions();
      };
      evtSource.onerror = () => setConnected(false);
      evtSource.addEventListener("session_update", (event) => {
        try {
          const updated = JSON.parse(event.data);
          if (updated.excluded) {
            // A project with .placet-off (RF-45): hide it and what belongs to it.
            setSessions((prev) => prev.filter((s) => s.id !== updated.id));
            setRequests((prev) =>
              prev.filter((r) => r.sender_session_id !== updated.id && r.recipient_session_id !== updated.id)
            );
            return;
          }
          if (!sessionsRef.current.some((s) => s.id === updated.id)) fetchRequests(); // new, or back from an exclusion
          setSessions((prev) => upsert(prev, updated));
        } catch (err) {
          console.error("Error parsing SSE event:", err);
        }
      });
      evtSource.addEventListener("request_update", (event) => {
        try {
          const updated = JSON.parse(event.data);
          setRequests((prev) => upsert(prev, updated));
        } catch (err) {
          console.error("Error parsing SSE event:", err);
        }
      });
    } catch (e) {
      console.error("SSE initialization error:", e);
    }
    return () => evtSource?.close();
  }, [fetchRequests, fetchSessions]);

  useEffect(() => {
    const onHashChange = () => {
      const id = requestIdFromHash(window.location.hash);
      if (id !== null) {
        jump.current = id;
        setFocusId(id);
        resolveJump(requestsRef.current);
        return;
      }
      setRoute(routeFromHash(window.location.hash));
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [resolveJump]);

  useEffect(() => resolveJump(requests), [requests, resolveJump]);

  useEffect(() => {
    if (scrollTo.current === null) return;
    const el = document.getElementById(`request-${scrollTo.current}`);
    if (el) {
      el.scrollIntoView?.({ block: "center" });
      scrollTo.current = null;
    }
  });

  const showFeedback = (text, isError = false) => {
    setFeedback({ text, isError });
    setTimeout(() => setFeedback(null), 4000);
  };

  const handleDecision = async (requestId, body) => {
    setSubmittingId(requestId);
    try {
      const res = await fetch(`/api/requests/${requestId}/${body.return_decision ? "return-decision" : "decision"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        showFeedback(
          { key: body.return_decision ? (body.approve ? "return.approved" : "return.rejected") : (body.status === "APPROVED" ? "requests.sent" : "requests.rejected"), params: { id: requestId } }
        );
        await fetchRequests();
      } else {
        const errData = await res.json().catch(() => ({}));
        showFeedback(apiErrorDescriptor(errData, res.status), true);
      }
    } catch (err) {
      console.error("Error deciding request:", err);
      showFeedback({ key: "errors.decisionConnection" }, true);
    } finally {
      setSubmittingId(null);
    }
  };

  const togglePin = async (session, pinned) => {
    setSessions((prev) => upsert(prev, { ...session, pinned }));
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(session.id)}/pin`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const updated = await res.json();
      setSessions((prev) => upsert(prev, updated));
    } catch {
      setSessions((prev) => upsert(prev, session));
      showFeedback({ key: "errors.pinSave" }, true);
    }
  };

  /** The user writes to a session (RF-34); true when the message is accepted. */
  const sendMessage = async (sessionId, text, mentions = [], attachments = [], mode = "queue") => {
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          ...(mentions.length ? { mentions } : {}),
          ...(attachments.length ? { attachments } : {}),
          ...(mode === "now" ? { mode } : {}),
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        showFeedback(apiErrorDescriptor(errData, res.status), true);
        return false;
      }
      const created = await res.json();
      setRequests((prev) => upsert(prev, created));
      return true;
    } catch {
      showFeedback({ key: "errors.messageConnection" }, true);
      return false;
    }
  };

  /**
   * Riprova / Annulla on a message whose delivery is uncertain or still waiting
   * (RF-29); "finished" / "queue_next" move a session's queue past a delivered
   * request whose end Placet cannot see (RF-29b).
   */
  const deliveryAction = async (requestId, action) => {
    if (action === "retry" && !window.confirm("Il messaggio potrebbe essere già arrivato: inviarlo di nuovo?")) {
      return false;
    }
    const release = action === "finished" || action === "queue_next";
    try {
      const res = release
        ? await fetch(`/api/requests/${requestId}/queue/release`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ reason: action }),
          })
        : await fetch(`/api/requests/${requestId}/delivery/${action}`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showFeedback(data.detail || `Operazione non riuscita (${res.status})`, true);
        return false;
      }
      setRequests((prev) => upsert(prev, data));
      showFeedback(
        {
          retry: "Nuovo tentativo di consegna.",
          cancel: "Messaggio annullato.",
          finished: "Segnata come terminata: la sessione riceve il prossimo messaggio.",
          queue_next: "Il prossimo messaggio parte senza aspettare.",
        }[action]
      );
      return true;
    } catch {
      showFeedback("Errore di connessione.", true);
      return false;
    }
  };

  const openSession = (id) => navigate(`#/sessione/${id}`);
  const pendingCount = requests.filter((r) => r.status === "PENDING" || needsReturnApproval(r)).length;

  const homeView = (
    <Home
      requests={requests}
      sessions={sessions}
      onDecide={handleDecision}
      submittingId={submittingId}
      focusId={focusId}
      onOpenSession={openSession}
      onTogglePin={togglePin}
      activeSessionId={route.view === "session" ? route.sessionId : null}
    />
  );
  const sessionView = route.view === "session" && (
    <SessionView
      key={route.sessionId}
      sessionId={route.sessionId}
      session={sessions.find((s) => s.id === route.sessionId)}
      requests={requests}
      sessions={sessions}
      onBack={() => navigate("#/")}
      onDecide={handleDecision}
      onSend={sendMessage}
      onDelivery={deliveryAction}
      submittingId={submittingId}
      focusId={focusId}
    />
  );
  const historyView = route.view === "history" && (
    <History requests={requests} sessions={sessions} focusId={focusId} onOpenSession={openSession} />
  );

  const navTabs = (
    <nav className="nav-tabs" data-testid="nav-tabs">
      <button
        data-testid="tab-home"
        className={`nav-tab ${route.view !== "history" ? "active" : ""}`}
        onClick={() => navigate("#/")}
      >
        <HomeIcon size={16} />
        {t("app.home")}
        {pendingCount > 0 && (
          <span className="badge-count" data-testid="pending-badge">
            {pendingCount}
          </span>
        )}
      </button>
      <button
        data-testid="tab-history"
        className={`nav-tab ${route.view === "history" ? "active" : ""}`}
        onClick={() => navigate("#/storico")}
      >
        <ShieldCheck size={16} />
        {t("app.history")}
      </button>
    </nav>
  );

  const layout = wide ? " wide" : route.view === "session" ? " in-session" : " with-bottom-bar";
  return (
    <div className={`app-container${layout}`}>
      <header>
        <div className="brand">
          <div className="logo-icon">P</div>
          <div>
            <h1>Placet</h1>
            <p className="brand-tagline">{t("app.tagline")}</p>
          </div>
        </div>
        {wide && navTabs}
        <div className="header-controls">
          <LanguageSelect />
          <div className="live-indicator" data-testid="live-indicator">
            <Radio size={12} className={connected ? "text-emerald-400 animate-pulse" : "text-amber-400"} />
            {t(connected ? "app.live" : "app.reconnecting")}
          </div>
        </div>
      </header>

      {feedback && (
        <div data-testid="feedback-banner" className={`feedback-banner${feedback.isError ? " error" : ""}`}>
          {feedback.isError ? <AlertCircle size={16} /> : <CheckCircle size={16} />}
          <span>{typeof feedback.text === "string" ? feedback.text : t(feedback.text.key, feedback.text.params)}</span>
        </div>
      )}

      {wide ? (
        // Desktop: questions and sessions on the left, the open conversation
        // (or the history) on the right.
        <div className="workspace">
          <aside className="sidebar" data-testid="sidebar">
            {homeView}
          </aside>
          <main className="pane" data-testid="pane">
            {sessionView || historyView || (
              <div className="pane-empty" data-testid="pane-empty">
                <MessagesSquare size={28} />
                <p>{t("app.openSession")}</p>
              </div>
            )}
          </main>
        </div>
      ) : (
        <>
          <main>
            {route.view === "home" && homeView}
            {historyView}
            {sessionView}
          </main>
          {/* Phone: the bar sits at the bottom, out of the way inside a conversation. */}
          {route.view !== "session" && navTabs}
        </>
      )}
    </div>
  );
}
