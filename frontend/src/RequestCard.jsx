import React, { useState } from "react";
import { ArrowRight, CheckCircle, XCircle } from "lucide-react";
import { OTHER, buildDecision, canApprove } from "./decisions";

export default function RequestCard({ req, submitting, highlighted, onDecide }) {
  const [editedPrompt, setEditedPrompt] = useState(undefined);
  const [selected, setSelected] = useState([]);
  const [otherText, setOtherText] = useState("");

  const isChoice = req.question_type === "single_choice" || req.question_type === "multi_choice";
  const multi = req.question_type === "multi_choice";
  const state = { editedPrompt, selected, otherText };

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

      {req.context && (
        <div className="card-context">
          <strong>Contesto:</strong> {req.context}
        </div>
      )}

      {req.question_type === "prompt_review" ? (
        <>
          <label className="prompt-editor-label" htmlFor={`prompt-${req.id}`}>
            Prompt per {req.recipient_id} (modificabile prima dell'invio):
          </label>
          <textarea
            id={`prompt-${req.id}`}
            data-testid={`prompt-textarea-${req.id}`}
            className="prompt-textarea"
            value={editedPrompt !== undefined ? editedPrompt : req.prompt}
            onChange={(e) => setEditedPrompt(e.target.value)}
          />
        </>
      ) : (
        <p className="question-text" data-testid={`question-${req.id}`}>
          {req.prompt}
        </p>
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
