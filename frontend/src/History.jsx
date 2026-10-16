import React from "react";
import { ArrowRight } from "lucide-react";
import { Origin } from "./RequestCard";
import Timeline from "./Timeline";
import Markdown from "./Markdown";

const STATUS_LABELS = {
  APPROVED: "Approvata",
  REJECTED: "Rifiutata",
  DELIVERING: "In consegna",
  EXECUTING: "In esecuzione",
  COMPLETED: "Completata",
  DELIVERY_FAILED: "Consegna fallita",
  STALLED: "Bloccata",
  CANCELLED: "Annullata",
  EXPIRED: "Scaduta",
};

function answerOf(req) {
  return [
    ...(req.selected_options || []),
    ...(req.custom_text ? [req.options?.length ? `Altro: ${req.custom_text}` : `Nota: ${req.custom_text}`] : []),
  ].join(" · ");
}

/** Settled requests with their audit timeline (RF-13). */
export default function History({ requests, sessions, focusId, onOpenSession }) {
  const settled = requests.filter((r) => r.status !== "PENDING");
  return (
    <div data-testid="history-section">
      <h2 style={{ fontSize: "1rem", marginBottom: "1rem", fontWeight: 700 }}>Storico Decisioni & Log</h2>
      {settled.length === 0 ? (
        <div className="empty-state" data-testid="empty-history">
          <p>Nessuna richiesta completata nello storico.</p>
        </div>
      ) : (
        settled.map((req) => (
          <div
            key={req.id}
            id={`request-${req.id}`}
            className={`card${req.id === focusId ? " card-highlight" : ""}`}
            data-testid={`history-card-${req.id}`}
          >
            <div className="card-header">
              <div className="agent-route">
                <span className="agent-badge">{req.sender_id}</span>
                <ArrowRight size={14} style={{ color: "var(--text-muted)" }} />
                <span className="agent-badge">{req.recipient_id}</span>
              </div>
              <span className={`status-badge status-${req.status.toLowerCase()}`}>
                {STATUS_LABELS[req.status] || req.status}
              </span>
            </div>
            <Origin req={req} sessions={sessions} onOpenSession={onOpenSession} />
            {req.context && (
              <section className="request-section">
                <h3 className="section-label">Contesto</h3>
                <div className="card-context">
                  <Markdown>{req.context}</Markdown>
                </div>
              </section>
            )}
            <section className="request-section">
              <h3 className="section-label">
                {req.recipient_id === "human"
                  ? "Domanda"
                  : req.edited_prompt
                    ? `Prompt inviato a ${req.recipient_id} (modificato da te)`
                    : `Prompt per ${req.recipient_id}`}
              </h3>
              <Markdown>{req.edited_prompt || req.prompt}</Markdown>
            </section>
            {(req.selected_options?.length > 0 || req.custom_text) && (
              <p data-testid={`history-answer-${req.id}`} style={{ fontSize: "0.85rem", marginBottom: "0.6rem" }}>
                <strong>Risposta:</strong> {answerOf(req)}
              </p>
            )}
            {req.response && (
              <div className="history-response">
                <strong>Risposta ({req.response.responder_id}):</strong>{" "}
                {req.response.implicit && (
                  <span
                    className="status-badge implicit-badge"
                    data-testid={`implicit-${req.id}`}
                    title="Ultimo messaggio dell'agente, non inviato con placet_reply"
                  >
                    implicita
                  </span>
                )}{" "}
                {req.response.content}
              </div>
            )}
            <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.6rem" }}>
              Creato: {new Date(req.created_at).toLocaleString()}
            </div>
            <Timeline requestId={req.id} />
          </div>
        ))
      )}
    </div>
  );
}
