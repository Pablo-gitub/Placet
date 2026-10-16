import React, { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle, Home as HomeIcon, Radio, ShieldCheck } from "lucide-react";
import Home from "./Home";
import History from "./History";
import SessionView from "./SessionView";
import { requestIdFromHash, routeFromHash } from "./decisions";

function upsert(list, item) {
  const index = list.findIndex((x) => x.id === item.id);
  if (index < 0) return [item, ...list];
  const next = [...list];
  next[index] = item;
  return next;
}

/** Where a notification link to a request leads: its session, or home / history. */
function targetOf(req) {
  if (req.status === "PENDING") return req.sender_session_id ? `#/sessione/${req.sender_session_id}` : "#/";
  return "#/storico";
}

export default function App() {
  const [requests, setRequests] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [route, setRoute] = useState(() => routeFromHash(window.location.hash));
  const [focusId, setFocusId] = useState(() => requestIdFromHash(window.location.hash));
  const [connected, setConnected] = useState(false);
  const [submittingId, setSubmittingId] = useState(null);
  const [feedback, setFeedback] = useState(null);
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
      const res = await fetch(`/api/requests/${requestId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        showFeedback(
          body.status === "APPROVED" ? `Richiesta #${requestId} inviata.` : `Richiesta #${requestId} rifiutata.`
        );
        await fetchRequests();
      } else {
        const errData = await res.json().catch(() => ({}));
        showFeedback(errData.detail || `Errore durante l'operazione (${res.status})`, true);
      }
    } catch (err) {
      console.error("Error deciding request:", err);
      showFeedback("Errore di connessione durante l'invio della decisione.", true);
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
      showFeedback("Non sono riuscito a salvare la scelta.", true);
    }
  };

  const openSession = (id) => navigate(`#/sessione/${id}`);
  const pendingCount = requests.filter((r) => r.status === "PENDING").length;

  return (
    <div className={`app-container${route.view === "session" ? " in-session" : ""}`}>
      <header>
        <div className="brand">
          <div className="logo-icon">P</div>
          <div>
            <h1>Placet</h1>
            <p style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Human Supervision & Agent Bus</p>
          </div>
        </div>
        <div className="live-indicator" data-testid="live-indicator">
          <Radio size={12} className={connected ? "text-emerald-400 animate-pulse" : "text-amber-400"} />
          {connected ? "LIVE" : "RICONNESSIONE…"}
        </div>
      </header>

      {feedback && (
        <div data-testid="feedback-banner" className={`feedback-banner${feedback.isError ? " error" : ""}`}>
          {feedback.isError ? <AlertCircle size={16} /> : <CheckCircle size={16} />}
          <span>{feedback.text}</span>
        </div>
      )}

      {route.view !== "session" && (
        <nav className="nav-tabs">
          <button
            data-testid="tab-home"
            className={`nav-tab ${route.view === "home" ? "active" : ""}`}
            onClick={() => navigate("#/")}
          >
            <HomeIcon size={16} />
            Home
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
            Storico
          </button>
        </nav>
      )}

      <main>
        {route.view === "home" && (
          <Home
            requests={requests}
            sessions={sessions}
            onDecide={handleDecision}
            submittingId={submittingId}
            focusId={focusId}
            onOpenSession={openSession}
            onTogglePin={togglePin}
          />
        )}
        {route.view === "history" && (
          <History requests={requests} sessions={sessions} focusId={focusId} onOpenSession={openSession} />
        )}
        {route.view === "session" && (
          <SessionView
            key={route.sessionId}
            sessionId={route.sessionId}
            session={sessions.find((s) => s.id === route.sessionId)}
            requests={requests}
            sessions={sessions}
            onBack={() => navigate("#/")}
            onDecide={handleDecision}
            submittingId={submittingId}
            focusId={focusId}
          />
        )}
      </main>
    </div>
  );
}
