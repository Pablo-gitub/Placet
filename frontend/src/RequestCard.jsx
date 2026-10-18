import React, { useState } from "react";
import { ArrowRight, CheckCircle, XCircle, Pencil, Eye, MessagesSquare } from "lucide-react";
import Markdown from "./Markdown";
import { OTHER, buildDecision, canApprove, needsReturnApproval } from "./decisions";
import { sessionName, sessionOrigin, sessionStatus } from "./sessionInfo";

/** Where a request comes from: the sender's session, when known; opens its conversation. */
export function Origin({ req, sessions, onOpenSession }) {
  const origin = sessionOrigin(sessions.find((s) => s.id === req.sender_session_id));
  if (!origin) return null;
  return (
    <div className="request-origin" data-testid={`origin-${req.id}`}>
      da {req.sender_id} · {origin}
      {onOpenSession && (
        <button
          type="button"
          className="link-button"
          data-testid={`open-session-${req.id}`}
          onClick={() => onOpenSession(req.sender_session_id)}
        >
          <MessagesSquare size={13} /> Apri la conversazione
        </button>
      )}
    </div>
  );
}

function ReturnCard({ req, submitting, onDecide }) {
  const [text, setText] = useState(req.response.content);
  return <div className="card" id={`request-${req.id}`} data-testid={`return-card-${req.id}`}>
    <h3>Risposta di {req.recipient_id} a {req.sender_id} · #{req.id}</h3>
    <p>Compito completato. La risposta attende il tuo consenso prima di raggiungere l’agente richiedente.</p>
    <details><summary>Richiesta originale</summary><Markdown>{req.edited_prompt || req.prompt}</Markdown></details>
    <label className="prompt-editor-label" htmlFor={`return-${req.id}`}>Risposta da inoltrare</label>
    <textarea id={`return-${req.id}`} className="prompt-textarea" value={text}
      onChange={(e) => setText(e.target.value)} />
    <div className="actions-row">
      <button className="btn btn-primary" disabled={submitting || !text.trim()}
        onClick={() => onDecide(req.id, { return_decision: true, approve: true, edited_response: text })}>Inoltra risposta</button>
      <button className="btn btn-danger" disabled={submitting}
        onClick={() => onDecide(req.id, { return_decision: true, approve: false })}>Non inoltrare</button>
    </div>
  </div>;
}

export default function RequestCard(props) {
  return needsReturnApproval(props.req) ? <ReturnCard {...props} /> : <PromptCard {...props} />;
}

function PromptCard({ req, submitting, highlighted, onDecide, sessions = [], onOpenSession }) {
  const [editedPrompt, setEditedPrompt] = useState(undefined);
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState([]);
  const [otherText, setOtherText] = useState("");
  const [targetSession, setTargetSession] = useState(req.recipient_session_id || "");
  const [allowAutoReturn, setAllowAutoReturn] = useState(false);
  const [deliveryMode, setDeliveryMode] = useState("queue");
  const targetSessions = sessions.filter((s) => s.agent_id === req.recipient_id && !s.ended_at && s.id !== req.sender_session_id);
  const target = targetSessions.find((s) => s.id === targetSession);
  const canSteer = target?.state === "busy" && ["claude-code", "antigravity"].includes(target.provider);

  const isChoice = req.question_type === "single_choice" || req.question_type === "multi_choice";
  const multi = req.question_type === "multi_choice";
  const state = { allowAutoReturn, editedPrompt, selected, otherText, targetSession, deliveryMode: canSteer ? deliveryMode : "queue" };

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

      <Origin req={req} sessions={sessions} onOpenSession={onOpenSession} />

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
                onChange={(e) => { setTargetSession(e.target.value); setDeliveryMode("queue"); }}
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
          {canSteer && (
            <fieldset className="request-section" data-testid={`delivery-mode-${req.id}`} disabled={submitting}>
              <legend className="section-label">Quando consegnare</legend>
              <label className="option-item">
                <input type="radio" name={`delivery-${req.id}`} checked={deliveryMode === "queue"}
                  onChange={() => setDeliveryMode("queue")} />
                <span>In coda · a fine turno</span>
              </label>
              <label className="option-item">
                <input type="radio" name={`delivery-${req.id}`} checked={deliveryMode === "now"}
                  onChange={() => setDeliveryMode("now")} />
                <span>Subito · nel turno in corso</span>
              </label>
              <p className="composer-note">Subito aggiunge il messaggio alla prossima azione utile. Se il turno termina prima, resta in coda.</p>
            </fieldset>
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

      {req.question_type === "prompt_review" && req.recipient_id !== "human" && req.sender_id !== "human" && (
        <label className="option-item">
          <input type="checkbox" checked={allowAutoReturn} onChange={(e) => setAllowAutoReturn(e.target.checked)} />
          <span>Consenti anche il ritorno automatico della risposta</span>
        </label>
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
