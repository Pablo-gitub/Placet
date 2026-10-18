import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Tag, X } from "lucide-react";
import { apiErrorDescriptor } from "./i18n/errors";

// Same rule as the backend: letters, digits, spaces, dots, dashes, underscores.
const NODE_NAME = /^[\p{L}\p{N}_][\p{L}\p{N}_ .-]{0,39}$/u;

async function call(method, url, body) {
  const res = await fetch(url, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw Object.assign(new Error("Node request failed"), { display: apiErrorDescriptor(data, res.status) });
  }
  return res.status === 204 ? null : res.json();
}

function NodeDialog({ session, onClose }) {
  const { t } = useTranslation();
  const [nodes, setNodes] = useState([]);
  const [name, setName] = useState(session.node_name || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => {
    let cancelled = false;
    call("GET", "/api/nodes")
      .then((list) => { if (!cancelled) setNodes(list); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const run = async (action) => {
    setBusy(true); setError(null);
    try { await action(); onClose(); }
    catch (e) { setError(e.display || { key: "errors.nodeSave" }); }
    finally { setBusy(false); }
  };
  const trimmed = name.trim();
  const valid = NODE_NAME.test(trimmed);
  const save = () => run(() => (session.node_id
    ? call("PATCH", `/api/nodes/${session.node_id}`, { name: trimmed })
    : call("POST", "/api/nodes", { name: trimmed, session_id: session.id })));
  const attach = (nodeId) => run(() => call("PUT", `/api/sessions/${encodeURIComponent(session.id)}/node`, { node_id: nodeId }));
  const others = nodes.filter((n) => n.provider === session.provider && n.id !== session.node_id);

  return createPortal(
    <div className="dialog-backdrop" onClick={() => !busy && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={t("node.title")} onClick={(e) => e.stopPropagation()}>
        <div className="dialog-header">
          <h2>{t("node.title")}</h2>
          <button type="button" className="icon-button" aria-label={t("picker.close")} disabled={busy} onClick={onClose}><X size={18} /></button>
        </div>
        <div className="dialog-body">
          <p>{t("node.explanation")}</p>
          <label className="prompt-editor-label" htmlFor="node-name">{t("node.name")}</label>
          <input id="node-name" className="session-select" value={name} maxLength={40} disabled={busy}
            data-testid="node-name" onChange={(e) => setName(e.target.value)} />
          {trimmed && !valid && <p className="field-hint">{t("node.nameRule")}</p>}
          <button type="button" className="btn btn-primary" data-testid="node-save"
            disabled={busy || !valid || trimmed === session.node_name} onClick={save}>
            {t(session.node_id ? "node.rename" : "node.create")}
          </button>
          {others.length > 0 && <>
            <label className="prompt-editor-label" htmlFor="node-attach">{t("node.attachLabel")}</label>
            <select id="node-attach" className="session-select" defaultValue="" disabled={busy}
              data-testid="node-attach" onChange={(e) => e.target.value && attach(Number(e.target.value))}>
              <option value="">{t("node.attachChoose")}</option>
              {others.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
            </select>
          </>}
          {session.node_id && <div className="dialog-actions">
            <button type="button" className="btn btn-secondary" disabled={busy} data-testid="node-detach"
              onClick={() => attach(null)}>{t("node.detach")}</button>
            <button type="button" className="btn btn-secondary" disabled={busy} data-testid="node-delete"
              onClick={() => window.confirm(t("node.deleteConfirm", { name: session.node_name }))
                && run(() => call("DELETE", `/api/nodes/${session.node_id}`))}>{t("node.delete")}</button>
          </div>}
          {error && <p role="alert">{t(error.key, error.params)}</p>}
        </div>
      </div>
    </div>, document.body,
  );
}

/** RFC-02: the session's node, as a chip that opens the node dialog. */
export default function NodeControl({ session }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!session) return null;
  return <>
    <button type="button" className={`node-chip${session.node_name ? " named" : ""}`} data-testid="node-chip"
      aria-label={session.node_name ? t("node.chip", { name: session.node_name }) : t("node.title")}
      title={session.node_name ? t("node.chip", { name: session.node_name }) : t("node.title")}
      onClick={() => setOpen(true)}>
      <Tag size={13} aria-hidden="true" />
      {session.node_name && <span>{session.node_name}</span>}
    </button>
    {open && <NodeDialog session={session} onClose={() => setOpen(false)} />}
  </>;
}
