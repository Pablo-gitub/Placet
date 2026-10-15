import React, { useState } from "react";
import { ArrowRight, CheckCircle, XCircle, Pencil, Eye } from "lucide-react";
import Markdown from "./Markdown";
import { OTHER, buildDecision, canApprove } from "./decisions";
import { sessionName, sessionOrigin, sessionStatus } from "./sessionInfo";

/** Where a request comes from: the sender's session, when known. */
export function Origin({ req, sessions }) {
  const origin = sessionOrigin(sessions.find((s) => s.id === req.sender_session_id));
  if (!origin) return null;
  return (
    <div className="request-origin" data-testid={`origin-${req.id}`}>
      da {req.sender_id} · {origin}
    </div>
  );
}

export default function RequestCard({ req, submitting, highlighted, onDecide, sessions = [] }) {
  const [editedPrompt, setEditedPrompt] = useState(undefined);
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState([]);
  const [otherText, setOtherText] = useState("");
  const [targetSession, setTargetSession] = useState(req.recipient_session_id || "");
  const targetSessions = sessions.filter((s) => s.agent_id === req.recipient_id && !s.ended_at);

  const isChoice = req.question_type === "single_choice" || req.question_type === "multi_choice";
  const multi = req.question_type === "multi_choice";
  const state = { editedPrompt, selected, otherText, targetSession };

  const toggle = (value) => {
    if (!multi) {
      setSelected([value]);
      return;
    }
    setSelected((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]
    );
  };

  const decide = (status) => onDecide(req.id, buildDecision(req, status, state));

  const approveLabel = req.question_type === "prompt_review" ? "Approva e invia" : isChoice ? "Rispondi" : "Approva";

  return (
    <div
      id={`request-${req.id}`}
      className={`card${highlighted ? " card-highlight" : ""}`}
      data-testid={`request-card-${req.id}`}
    >
      <div className="card-header">
        <div className="agent-route">
          <span className="agent-badge">{req.sender_id}</span>
          <ArrowRight size={14} style={{ color: "var(--text-muted)" }} />
          <span className="agent-badge">{req.recipient_id}</span>
        </div>
        <div className="badges">
          {(req.priority === "high" || req.priority === "urgent") && (
            <span className={`status-badge priority-${req.priority}`} data-testid={`priority-${req.id}`}>
              {req.priority === "urgent" ? "Urgente" : "Alta"}
            </span>
          )}
          <span className="status-badge status-pending">In attesa</span>
        </div>
      </div>

      <Origin req={req} sessions={sessions} />

      {req.context && (
        <section className="request-section">
          <h3 className="section-label">Contesto</h3>
          <div className="card-context">
            <Markdown testId={`context-${req.id}`}>{req.context}</Markdown>
          </div>
        </section>
      )}

      {req.question_type === "prompt_review" ? (
        <>
          <section className="request-section">
            <div className="section-head">
              <h3 className="section-label">
                Prompt per {req.recipient_id}
                {req.prompt_source && (
                  <span className="prompt-source" data-testid={`prompt-source-${req.id}`}>
                    {" "}· da {req.prompt_source}
                  </span>
                )}
              </h3>
              <button
                type="button"
                className="link-button"
                data-testid={`toggle-edit-${req.id}`}
                onClick={() => setEditing((v) => !v)}
              >
                {editing ? <Eye size={14} /> : <Pencil size={14} />}
                {editing ? "Anteprima" : "Modifica"}
              </button>
            </div>
            {editing ? (
              <textarea
                aria-label={`Prompt per ${req.recipient_id}`}
                data-testid={`prompt-textarea-${req.id}`}
                className="prompt-textarea"
                value={editedPrompt !== undefined ? editedPrompt : req.prompt}
                onChange={(e) => setEditedPrompt(e.target.value)}
              />
            ) : (
              <div className="prompt-preview">
                <Markdown testId={`prompt-preview-${req.id}`}>
                  {editedPrompt !== undefined ? editedPrompt : req.prompt}
                </Markdown>
              </div>
            )}
            {editedPrompt !== undefined && editedPrompt !== req.prompt && (
              <p className="edited-note">Modificato da te: {req.recipient_id} riceverà questa versione.</p>
            )}
          </section>
          <section className="request-section">
            <h3 className="section-label">Domanda</h3>
            <p className="question-text">
              Inviare questo prompt a {req.recipient_id}? Riceverà solo il prompt, non il contesto.
            </p>
          </section>
          {targetSessions.length > 0 && (
            <>
              <label className="prompt-editor-label" htmlFor={`target-${req.id}`}>
                Sessione di {req.recipient_id} che lo riceve:
              </label>
              <select
                id={`target-${req.id}`}
                data-testid={`target-session-${req.id}`}
                className="session-select"
                value={targetSession}
                onChange={(e) => setTargetSession(e.target.value)}
              >
                <option value="">Automatica (la prima libera)</option>
                {targetSessions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {sessionName(s)} · {sessionStatus(s).label}
                  </option>
                ))}
              </select>
            </>
          )}
        </>
      ) : (
        <section className="request-section">
          <h3 className="section-label">Domanda</h3>
          <div className="question-text">
            <Markdown testId={`question-${req.id}`}>{req.prompt}</Markdown>
          </div>
        </section>
      )}

      {isChoice && (
        <fieldset className="options-list">
          <legend className="prompt-editor-label">
            {multi ? "Scegli una o più opzioni:" : "Scegli un'opzione:"}
          </legend>
          {[...req.options, OTHER].map((opt) => (
            <label key={opt} className="option-item">
              <input
                type={multi ? "checkbox" : "radio"}
                name={`opt-${req.id}`}
                value={opt}
                data-testid={`option-${req.id}-${opt === OTHER ? "altro" : opt}`}
                checked={selected.includes(opt)}
                onChange={() => toggle(opt)}
              />
              <span>{opt === OTHER ? "Altro…" : opt}</span>
            </label>
          ))}
        </fieldset>
      )}

      {(req.question_type === "confirmation" || (isChoice && selected.includes(OTHER))) && (
        <textarea
          data-testid={`free-text-${req.id}`}
          className="prompt-textarea free-text"
          placeholder={isChoice ? "Scrivi la tua risposta" : "Nota per l'agente (facoltativa)"}
          aria-label={isChoice ? "Risposta Altro" : "Nota per l'agente"}
          value={otherText}
          onChange={(e) => setOtherText(e.target.value)}
        />
      )}

      <div className="actions-row">
        <button
          data-testid={`btn-approve-${req.id}`}
          className="btn btn-primary"
          disabled={submitting || !canApprove(req, state)}
          onClick={() => decide("APPROVED")}
        >
          <CheckCircle size={16} />
          {submitting ? "Invio in corso..." : approveLabel}
        </button>
        <button
          data-testid={`btn-reject-${req.id}`}
          className="btn btn-danger"
          disabled={submitting}
          onClick={() => decide("REJECTED")}
        >
          <XCircle size={16} />
          Rifiuta
        </button>
      </div>
    </div>
  );
}
