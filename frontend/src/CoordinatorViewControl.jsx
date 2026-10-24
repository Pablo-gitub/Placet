import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { apiErrorDescriptor } from "./i18n/errors";
import { formatWhen } from "./i18n/formatters";

const ROLE_MAX = 500;

async function call(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    const err = new Error(data?.detail || "Request failed");
    err.display = apiErrorDescriptor(data, res.status);
    throw err;
  }
}

/**
 * RFC-17, per node of a project: the public role other nodes may read, and the switch that lets the node see the
 * project as a coordinator (off by default; it needs a verified session and authorizes no send).
 */
export default function CoordinatorViewControl({ projectId, node, onChanged }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const saved = node.public_role || "";
  const role = draft ?? saved;
  const base = `/api/projects/${projectId}/nodes/${node.id}`;

  const run = async (action) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged?.();
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: 500 } });
    } finally {
      setBusy(false);
    }
  };

  const pendingSession = node.coordinator_view && node.coordinator_session?.state === "pending_confirmation"
    ? node.coordinator_session.session : null;
  const confirm = () => run(() => call("POST", `${base}/view-grant/confirm`, { session_id: pendingSession.id }));
  const toggle = () => run(() => call(node.coordinator_view ? "DELETE" : "PUT", `${base}/view-grant`));
  const saveRole = () => run(async () => {
    await call("PUT", `${base}/public-role`, { text: role.trim() || null });
    setDraft(null);
  });

  return (
    <div className="project-node-coordinator" data-testid={`coordinator-${node.id}`}>
      <label className="prompt-editor-label" htmlFor={`public-role-${node.id}`}>{t("projects.publicRoleTitle")}</label>
      <textarea
        id={`public-role-${node.id}`}
        data-testid={`public-role-input-${node.id}`}
        className="prompt-textarea"
        maxLength={ROLE_MAX}
        rows={2}
        placeholder={t("projects.publicRolePlaceholder")}
        value={role}
        onChange={(e) => setDraft(e.target.value)}
      />
      <small className="composer-note" data-testid={`public-role-hint-${node.id}`}>{t("projects.publicRoleHint")}</small>
      <div className="project-instructions-actions">
        <span className="char-counter">{role.length} / {ROLE_MAX}</span>
        <button type="button" className={`btn ${role.trim() !== saved ? "btn-primary" : "btn-secondary"}`}
          disabled={busy || role.trim() === saved} data-testid={`save-public-role-btn-${node.id}`} onClick={saveRole}>
          {t("projects.savePublicRole")}
        </button>
      </div>
      <label className="coordinator-switch">
        <input type="checkbox" role="switch" checked={!!node.coordinator_view} disabled={busy} onChange={toggle} />
        <span>{t("projects.coordinatorView")}</span>
      </label>
      <small className="composer-note" data-testid={`coordinator-hint-${node.id}`}>{t("projects.coordinatorHint")}</small>
      {node.coordinator_supported === false && (
        <small className="composer-note error-text" data-testid={`coordinator-unsupported-${node.id}`}>
          {t("projects.coordinatorUnsupported", { provider: node.provider })}
        </small>
      )}
      {node.coordinator_view && node.coordinator_session?.state === "no_session" && (
        <small className="composer-note" data-testid={`coordinator-nosession-${node.id}`}>{t("projects.coordinatorNoSession")}</small>
      )}
      {pendingSession && (
        <div className="coordinator-pending" data-testid={`coordinator-pending-${node.id}`}>
          <p>
            {t("projects.coordinatorPending", {
              title: pendingSession.title || pendingSession.id.slice(0, 8), provider: pendingSession.provider,
              when: formatWhen(pendingSession.started_at),
            })}
          </p>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={confirm}>
            {t("projects.confirmSession")}
          </button>
        </div>
      )}
      {error && <p role="alert" className="error-message">{t(error.key, error.params)}</p>}
    </div>
  );
}
