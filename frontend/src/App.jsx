import React, { useState, useEffect } from "react";
import {
  CheckCircle,
  XCircle,
  Radio,
  Send,
  Bot,
  User,
  Clock,
  ArrowRight,
  ShieldCheck,
  RefreshCw,
} from "lucide-react";

export default function App() {
  const [activeTab, setActiveTab] = useState("inbox");
  const [requests, setRequests] = useState([]);
  const [agentsData, setAgentsData] = useState({ agents: {}, rules: {} });
  const [editedPrompts, setEditedPrompts] = useState({});
  const [selectedOpts, setSelectedOpts] = useState({});
  const [connected, setConnected] = useState(false);

  const fetchRequests = async () => {
    try {
      const res = await fetch("/api/requests");
      if (res.ok) {
        const data = await res.json();
        setRequests(data);
      }
    } catch (e) {
      console.error("Error fetching requests:", e);
    }
  };

  const fetchAgents = async () => {
    try {
      const res = await fetch("/api/agents");
      if (res.ok) {
        const data = await res.json();
        setAgentsData(data);
      }
    } catch (e) {
      console.error("Error fetching agents:", e);
    }
  };

  useEffect(() => {
    fetchRequests();
    fetchAgents();

    // Setup Server-Sent Events (SSE)
    const evtSource = new EventSource("/api/events");
    evtSource.onopen = () => setConnected(true);
    evtSource.onerror = () => setConnected(false);

    evtSource.addEventListener("request_update", (event) => {
      try {
        const updated = JSON.parse(event.data);
        setRequests((prev) => {
          const index = prev.findIndex((r) => r.id === updated.id);
          if (index >= 0) {
            const next = [...prev];
            next[index] = updated;
            return next;
          } else {
            return [updated, ...prev];
          }
        });
      } catch (err) {
        console.error("Error parsing SSE event:", err);
      }
    });

    return () => {
      evtSource.close();
    };
  }, []);

  const handleDecision = async (requestId, status) => {
    const editedPrompt = editedPrompts[requestId];
    const opts = selectedOpts[requestId] ? [selectedOpts[requestId]] : [];

    try {
      const res = await fetch(`/api/requests/${requestId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status,
          edited_prompt: editedPrompt,
          selected_options: opts,
        }),
      });
      if (res.ok) {
        fetchRequests();
      }
    } catch (err) {
      console.error("Error deciding request:", err);
    }
  };

  const pendingRequests = requests.filter((r) => r.status === "PENDING");
  const historyRequests = requests.filter((r) => r.status !== "PENDING");

  return (
    <div className="app-container">
      <header>
        <div className="brand">
          <div className="logo-icon">P</div>
          <div>
            <h1>Placet</h1>
            <p style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
              Human Supervision & Agent Bus
            </p>
          </div>
        </div>
        <div className="live-indicator">
          <Radio size={12} className={connected ? "text-emerald-400 animate-pulse" : "text-amber-400"} />
          {connected ? "LIVE TAILSCALE" : "CONNECTING..."}
        </div>
      </header>

      <nav className="nav-tabs">
        <button
          className={`nav-tab ${activeTab === "inbox" ? "active" : ""}`}
          onClick={() => setActiveTab("inbox")}
        >
          <Clock size={16} />
          Inbox
          {pendingRequests.length > 0 && (
            <span className="badge-count">{pendingRequests.length}</span>
          )}
        </button>
        <button
          className={`nav-tab ${activeTab === "agents" ? "active" : ""}`}
          onClick={() => setActiveTab("agents")}
        >
          <Bot size={16} />
          Agenti
        </button>
        <button
          className={`nav-tab ${activeTab === "history" ? "active" : ""}`}
          onClick={() => setActiveTab("history")}
        >
          <ShieldCheck size={16} />
          Storico
        </button>
      </nav>

      <main>
        {activeTab === "inbox" && (
          <div>
            {pendingRequests.length === 0 ? (
              <div className="empty-state">
                <CheckCircle size={40} style={{ margin: "0 auto 0.75rem", color: "var(--success)" }} />
                <h3>Nessuna richiesta in attesa</h3>
                <p>Tutti gli agenti sono allineati o in attesa di nuovi compiti.</p>
              </div>
            ) : (
              pendingRequests.map((req) => (
                <div key={req.id} className="card">
                  <div className="card-header">
                    <div className="agent-route">
                      <span className="agent-badge">{req.sender_id}</span>
                      <ArrowRight size={14} style={{ color: "var(--text-muted)" }} />
                      <span className="agent-badge">{req.recipient_id}</span>
                    </div>
                    <span className="status-badge status-pending">In Attesa</span>
                  </div>

                  {req.context && (
                    <div className="card-context">
                      <strong>Contesto:</strong> {req.context}
                    </div>
                  )}

                  <label className="prompt-editor-label">
                    {req.recipient_id === "human"
                      ? "Domanda / Scelta per te:"
                      : "Prompt proposto per l'agente (modificabile prima dell'invio):"}
                  </label>
                  <textarea
                    className="prompt-textarea"
                    value={
                      editedPrompts[req.id] !== undefined
                        ? editedPrompts[req.id]
                        : req.prompt
                    }
                    onChange={(e) =>
                      setEditedPrompts({
                        ...editedPrompts,
                        [req.id]: e.target.value,
                      })
                    }
                  />

                  {req.options && req.options.length > 0 && (
                    <div className="options-list">
                      <label className="prompt-editor-label">Opzioni di scelta:</label>
                      {req.options.map((opt, idx) => (
                        <label key={idx} className="option-item">
                          <input
                            type="radio"
                            name={`opt-${req.id}`}
                            value={opt}
                            checked={selectedOpts[req.id] === opt}
                            onChange={() =>
                              setSelectedOpts({ ...selectedOpts, [req.id]: opt })
                            }
                          />
                          <span>{opt}</span>
                        </label>
                      ))}
                    </div>
                  )}

                  <div className="actions-row">
                    <button
                      className="btn btn-primary"
                      onClick={() => handleDecision(req.id, "APPROVED")}
                    >
                      <CheckCircle size={16} />
                      Approva ed Invia
                    </button>
                    <button
                      className="btn btn-danger"
                      onClick={() => handleDecision(req.id, "REJECTED")}
                    >
                      <XCircle size={16} />
                      Rifiuta
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {activeTab === "agents" && (
          <div>
            <h2 style={{ fontSize: "1rem", marginBottom: "1rem", fontWeight: 700 }}>
              Sessioni e Agenti Registrati
            </h2>
            {Object.entries(agentsData.agents || {}).map(([id, ag]) => (
              <div key={id} className="card">
                <div className="card-header">
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                    <Bot size={20} style={{ color: "var(--accent)" }} />
                    <strong>{ag.name || id}</strong>
                    <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                      ({id})
                    </span>
                  </div>
                  <span className="status-badge status-approved">
                    tmux: {ag.tmux_session}
                  </span>
                </div>
                <div style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
                  <p>
                    <strong>Destinatari ammessi:</strong>{" "}
                    {ag.allowed_targets.join(", ") || "Nessuno"}
                  </p>
                  <p style={{ marginTop: "0.3rem" }}>
                    <strong>Timeout predefinito:</strong> {ag.default_timeout_seconds}s
                  </p>
                </div>
              </div>
            ))}

            <h2 style={{ fontSize: "1rem", margin: "1.5rem 0 1rem", fontWeight: 700 }}>
              Regole di Approvazione Rotte
            </h2>
            {Object.entries(agentsData.rules || {}).map(([route, rule]) => (
              <div
                key={route}
                className="card"
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{route}</div>
                <span
                  className={`status-badge ${
                    rule.require_approval ? "status-pending" : "status-approved"
                  }`}
                >
                  {rule.require_approval ? "Supervisione Umana" : "Auto-Routing"}
                </span>
              </div>
            ))}
          </div>
        )}

        {activeTab === "history" && (
          <div>
            <h2 style={{ fontSize: "1rem", marginBottom: "1rem", fontWeight: 700 }}>
              Storico Decisioni & Log
            </h2>
            {historyRequests.length === 0 ? (
              <div className="empty-state">
                <p>Nessuna richiesta completata nello storico.</p>
              </div>
            ) : (
              historyRequests.map((req) => (
                <div key={req.id} className="card">
                  <div className="card-header">
                    <div className="agent-route">
                      <span className="agent-badge">{req.sender_id}</span>
                      <ArrowRight size={14} style={{ color: "var(--text-muted)" }} />
                      <span className="agent-badge">{req.recipient_id}</span>
                    </div>
                    <span
                      className={`status-badge status-${req.status.toLowerCase()}`}
                    >
                      {req.status}
                    </span>
                  </div>
                  <p style={{ fontSize: "0.9rem", marginBottom: "0.6rem" }}>
                    <strong>Prompt:</strong> {req.edited_prompt || req.prompt}
                  </p>
                  {req.response && (
                    <div
                      style={{
                        background: "rgba(16, 185, 129, 0.08)",
                        borderLeft: "3px solid var(--success)",
                        padding: "0.6rem 0.8rem",
                        borderRadius: "8px",
                        fontSize: "0.85rem",
                      }}
                    >
                      <strong>Risposta ({req.response.responder_id}):</strong>{" "}
                      {req.response.content}
                    </div>
                  )}
                  <div
                    style={{
                      fontSize: "0.75rem",
                      color: "var(--text-muted)",
                      marginTop: "0.6rem",
                    }}
                  >
                    Creato: {new Date(req.created_at).toLocaleString()}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </main>
    </div>
  );
}
