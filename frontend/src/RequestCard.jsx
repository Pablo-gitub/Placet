import { useTranslation } from "react-i18next";
import React, { useState } from "react";
import { ArrowRight, CheckCircle, XCircle, Pencil, Eye, MessagesSquare } from "lucide-react";
import Markdown from "./Markdown";
import { OTHER, buildDecision, canApprove, needsReturnApproval } from "./decisions";
import { sessionName, sessionOrigin, sessionStatus } from "./sessionInfo";
import { CreateRelation } from "./Relations";

/** Where a request comes from: the sender's session, when known; opens its conversation. */
export function Origin({ req, sessions, onOpenSession }) {
  const { t } = useTranslation();
  const origin = sessionOrigin(sessions.find((s) => s.id === req.sender_session_id));
  if (!origin) return null;
  return (
    <div className="request-origin" data-testid={`origin-${req.id}`}>
      {t("requests.origin", { agent: req.sender_id, origin })}
      {onOpenSession && (
        <button
          type="button"
          className="link-button"
          data-testid={`open-session-${req.id}`}
          onClick={() => onOpenSession(req.sender_session_id)}
        >
          <MessagesSquare size={13} /> {t("requests.openConversation")}
        </button>
      )}
    </div>
  );
}

function ReturnCard({ req, submitting, onDecide }) {
  const { t } = useTranslation();
  const [text, setText] = useState(req.response.content);
  return <div className="card" id={`request-${req.id}`} data-testid={`return-card-${req.id}`}>
    <h3>{t("return.title", { recipient: req.recipient_id, sender: req.sender_id, id: req.id })}</h3>
    <p>{t("return.explanation")}</p>
    {req.response.implicit && <p>{t("return.implicit")}</p>}
    <details><summary>{t("return.originalRequest")}</summary><Markdown>{req.edited_prompt || req.prompt}</Markdown></details>
    <label className="prompt-editor-label" htmlFor={`return-${req.id}`}>{t("return.label")}</label>
    <textarea id={`return-${req.id}`} className="prompt-textarea" value={text}
      onChange={(e) => setText(e.target.value)} />
    <div className="actions-row">
      <button className="btn btn-primary" disabled={submitting || !text.trim()}
        onClick={() => onDecide(req.id, { return_decision: true, approve: true, edited_response: text })}>{t("return.approve")}</button>
      <button className="btn btn-danger" disabled={submitting}
        onClick={() => onDecide(req.id, { return_decision: true, approve: false })}>{t("return.reject")}</button>
    </div>
  </div>;
}

export default function RequestCard(props) {
  return needsReturnApproval(props.req) ? <ReturnCard {...props} /> : <PromptCard {...props} />;
}

function PromptCard({ req, submitting, highlighted, onDecide, sessions = [], onOpenSession }) {
  const { t } = useTranslation();
  const [editedPrompt, setEditedPrompt] = useState(undefined);
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState([]);
  const [otherText, setOtherText] = useState("");
  const [targetSession, setTargetSession] = useState(req.recipient_session_id || "");
  const [allowAutoReturn, setAllowAutoReturn] = useState(false);
  const [deliveryMode, setDeliveryMode] = useState("queue");
  // The recipient agent's sessions first; the user may also choose another agent's.
  const openSessions = sessions.filter((s) => !s.ended_at && !s.excluded && s.id !== req.sender_session_id);
  const targetSessions = openSessions.filter((s) => s.agent_id === req.recipient_id);
  const otherSessions = req.recipient_id === "human" ? [] : openSessions.filter((s) => s.agent_id !== req.recipient_id);
  const target = openSessions.find((s) => s.id === targetSession);
  const redirected = target && target.agent_id !== req.recipient_id;
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

  const approveLabel = t(req.question_type === "prompt_review" ? "requests.approveSend" : isChoice ? "requests.answer" : "requests.approve");

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
              {t(req.priority === "urgent" ? "requests.urgent" : "requests.high")}
            </span>
          )}
          <span className="status-badge status-pending">{t("requests.pending")}</span>
        </div>
      </div>

      <Origin req={req} sessions={sessions} onOpenSession={onOpenSession} />

      {req.context && (
        <section className="request-section">
          <h3 className="section-label">{t("requests.context")}</h3>
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
                {t("requests.prompt", { agent: req.recipient_id })}
                {req.prompt_source && (
                  <span className="prompt-source" data-testid={`prompt-source-${req.id}`}>
                    {" "}{t("requests.promptSource", { source: req.prompt_source })}
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
                {t(editing ? "requests.preview" : "requests.edit")}
              </button>
            </div>
            {editing ? (
              <textarea
                aria-label={t("requests.prompt", { agent: req.recipient_id })}
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
              <p className="edited-note">{t("requests.edited", { agent: req.recipient_id })}</p>
            )}
          </section>
          <section className="request-section">
            <h3 className="section-label">{t("requests.question")}</h3>
            <p className="question-text">
              {t("requests.confirmPrompt", { agent: req.recipient_id })}
            </p>
          </section>
          {targetSessions.length + otherSessions.length > 0 && (
            <>
              <label className="prompt-editor-label" htmlFor={`target-${req.id}`}>
                {t("requests.recipient", { agent: req.recipient_id })}
              </label>
              <select
                id={`target-${req.id}`}
                data-testid={`target-session-${req.id}`}
                className="session-select"
                value={targetSession}
                onChange={(e) => { setTargetSession(e.target.value); setDeliveryMode("queue"); }}
              >
                <option value="">{t("requests.automatic")}</option>
                {targetSessions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {sessionName(s)} · {sessionStatus(s).label}
                  </option>
                ))}
                {otherSessions.length > 0 && (
                  <optgroup label={t("requests.otherRecipients")}>
                    {otherSessions.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.agent_id} · {sessionName(s)} · {sessionStatus(s).label}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
              {redirected && (
                <p className="composer-note" data-testid={`redirected-${req.id}`}>
                  {t("requests.redirected", { from: req.recipient_id, to: target.agent_id })}
                </p>
              )}
            </>
          )}
          {req.recipient_id !== "human" && !req.relation_id && (
            <CreateRelation sender={sessions.find((s) => s.id === req.sender_session_id)}
              recipient={sessions.find((s) => s.id === (targetSession || req.recipient_session_id))} />
          )}
          {canSteer && (
            <fieldset className="request-section" data-testid={`delivery-mode-${req.id}`} disabled={submitting}>
              <legend className="section-label">{t("requests.when")}</legend>
              <label className="option-item">
                <input type="radio" name={`delivery-${req.id}`} checked={deliveryMode === "queue"}
                  onChange={() => setDeliveryMode("queue")} />
                <span>{t("requests.queue")}</span>
              </label>
              <label className="option-item">
                <input type="radio" name={`delivery-${req.id}`} checked={deliveryMode === "now"}
                  onChange={() => setDeliveryMode("now")} />
                <span>{t("requests.now")}</span>
              </label>
              <p className="composer-note">{t("requests.nowHint")}</p>
            </fieldset>
          )}
        </>
      ) : (
        <section className="request-section">
          <h3 className="section-label">{t("requests.question")}</h3>
          <div className="question-text">
            <Markdown testId={`question-${req.id}`}>{req.prompt}</Markdown>
          </div>
        </section>
      )}

      {isChoice && (
        <fieldset className="options-list">
          <legend className="prompt-editor-label">
            {t(multi ? "requests.chooseMany" : "requests.chooseOne")}
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
              <span>{opt === OTHER ? t("requests.other") : opt}</span>
            </label>
          ))}
        </fieldset>
      )}

      {(req.question_type === "confirmation" || (isChoice && selected.includes(OTHER))) && (
        <textarea
          data-testid={`free-text-${req.id}`}
          className="prompt-textarea free-text"
          placeholder={t(isChoice ? "requests.writeAnswer" : "requests.notePlaceholder")}
          aria-label={t(isChoice ? "requests.otherLabel" : "requests.noteLabel")}
          value={otherText}
          onChange={(e) => setOtherText(e.target.value)}
        />
      )}

      {req.question_type === "prompt_review" && req.recipient_id !== "human" && req.sender_id !== "human" && (
        <label className="option-item">
          <input type="checkbox" checked={allowAutoReturn} onChange={(e) => setAllowAutoReturn(e.target.checked)} />
          <span>{t("requests.autoReturn")}</span>
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
          {submitting ? t("requests.submitting") : approveLabel}
        </button>
        <button
          data-testid={`btn-reject-${req.id}`}
          className="btn btn-danger"
          disabled={submitting}
          onClick={() => decide("REJECTED")}
        >
          <XCircle size={16} />
          {t("requests.reject")}
        </button>
      </div>
    </div>
  );
}
