import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, Link2, Trash2, X } from "lucide-react";
import { apiErrorDescriptor } from "./i18n/errors";
import { sessionName } from "./sessionInfo";

// Same rule as the backend for node and type names.
const NAME_RULE = /^[\p{L}\p{N}_][\p{L}\p{N}_ .-]{0,39}$/u;

async function api(method, url, body) {
  const res = await fetch(url, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw Object.assign(new Error("Request failed"), { display: apiErrorDescriptor(data, res.status) });
  }
  return res.status === 204 ? null : res.json();
}

function useList(url, version = 0) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    let cancelled = false;
    api("GET", url).then((list) => { if (!cancelled) setItems(list); }).catch(() => {});
    return () => { cancelled = true; };
  }, [url, version]);
  return items;
}

/**
 * One side of a new relation: an existing node, or a session still to be
 * named (its node is created on save).
 */
function Side({ side, label, name, setName, t, testId }) {
  if (side.nodeId) return <p className="relation-side"><small>{label}</small> <strong>{side.nodeName}</strong></p>;
  return <>
    <label className="prompt-editor-label" htmlFor={testId}>
      {t("relations.nameSession", { label, session: side.session ? sessionName(side.session) : "?" })}
    </label>
    <input id={testId} data-testid={testId} className="session-select" maxLength={40}
      value={name} onChange={(e) => setName(e.target.value)} />
  </>;
}

/** RFC-04/07: a relation A -> B of a type, with its approval policy. */
export function RelationForm({ from, to, onDone, onCancel }) {
  const { t } = useTranslation();
  const types = useList("/api/relation-types");
  const [fromName, setFromName] = useState("");
  const [toName, setToName] = useState("");
  const [typeId, setTypeId] = useState("");
  const [newType, setNewType] = useState("");
  const [description, setDescription] = useState("");
  const [approval, setApproval] = useState("manual");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const creatingType = typeId === "new" || types.length === 0;
  const valid = (from.nodeId || NAME_RULE.test(fromName.trim()))
    && (to.nodeId || NAME_RULE.test(toName.trim()))
    && (creatingType ? NAME_RULE.test(newType.trim()) : typeId !== "");

  const save = async () => {
    setBusy(true); setError(null);
    try {
      const fromNode = from.nodeId || (await api("POST", "/api/nodes", { name: fromName.trim(), session_id: from.session.id })).id;
      const toNode = to.nodeId || (await api("POST", "/api/nodes", { name: toName.trim(), session_id: to.session.id })).id;
      const type = creatingType
        ? (await api("POST", "/api/relation-types", { name: newType.trim(), description: description.trim() })).id
        : Number(typeId);
      await api("POST", "/api/relations", { from_node: fromNode, to_node: toNode, type_id: type, approval });
      onDone();
    } catch (e) {
      setError(e.display || { key: "errors.relationSave" });
    } finally { setBusy(false); }
  };

  return (
    <fieldset className="relation-form" disabled={busy} data-testid="relation-form">
      <Side side={from} label={t("relations.from")} name={fromName} setName={setFromName} t={t} testId="relation-from-name" />
      <Side side={to} label={t("relations.to")} name={toName} setName={setToName} t={t} testId="relation-to-name" />
      <label className="prompt-editor-label" htmlFor="relation-type">{t("relations.type")}</label>
      {types.length > 0 && (
        <select id="relation-type" data-testid="relation-type" className="session-select" value={typeId}
          onChange={(e) => setTypeId(e.target.value)}>
          <option value="">{t("relations.chooseType")}</option>
          {types.map((ty) => <option key={ty.id} value={ty.id}>{ty.name}</option>)}
          <option value="new">{t("relations.newType")}</option>
        </select>
      )}
      {creatingType && <>
        <input data-testid="relation-type-name" className="session-select" maxLength={40}
          placeholder={t("relations.typeName")} aria-label={t("relations.typeName")}
          value={newType} onChange={(e) => setNewType(e.target.value)} />
        <textarea data-testid="relation-type-description" className="prompt-textarea relation-description" maxLength={500}
          placeholder={t("relations.typeDescription")} aria-label={t("relations.typeDescription")}
          value={description} onChange={(e) => setDescription(e.target.value)} />
      </>}
      <h3 className="section-label">{t("relations.policy")}</h3>
      <label className="option-item">
        <input type="radio" name="relation-approval" checked={approval === "manual"} onChange={() => setApproval("manual")} />
        <span>{t("relations.manual")}</span>
      </label>
      <label className="option-item">
        <input type="radio" name="relation-approval" data-testid="relation-automatic"
          checked={approval === "automatic"} onChange={() => setApproval("automatic")} />
        <span>{t("relations.automatic")}</span>
      </label>
      {approval === "automatic" && <p className="composer-note">{t("relations.automaticHint")}</p>}
      <div className="dialog-actions">
        <button type="button" className="btn btn-primary" data-testid="relation-save" disabled={!valid} onClick={save}>
          {t("relations.create")}
        </button>
        {onCancel && <button type="button" className="btn btn-secondary" onClick={onCancel}>{t("relations.cancel")}</button>}
      </div>
      {error && <p role="alert">{t(error.key, error.params)}</p>}
    </fieldset>
  );
}

function RelationRow({ rel, onChanged }) {
  const { t } = useTranslation();
  const [error, setError] = useState(null);
  const run = async (method, body) => {
    setError(null);
    try { await api(method, `/api/relations/${rel.id}`, body); onChanged(); }
    catch (e) { setError(e.display || { key: "errors.relationSave" }); }
  };
  return (
    <li className="relation-row" data-testid={`relation-${rel.id}`}>
      <span className="relation-route">
        {rel.from_name} <ArrowRight size={12} aria-hidden="true" /> {rel.to_name}
        <small>{rel.type_name}</small>
      </span>
      <select className="session-select" aria-label={t("relations.policy")} value={rel.approval}
        data-testid={`relation-approval-${rel.id}`} onChange={(e) => run("PATCH", { approval: e.target.value })}>
        <option value="manual">{t("relations.manualShort")}</option>
        <option value="automatic">{t("relations.automaticShort")}</option>
      </select>
      <button type="button" className="icon-button" aria-label={t("relations.delete")} title={t("relations.delete")}
        onClick={() => window.confirm(t("relations.deleteConfirm")) && run("DELETE")}>
        <Trash2 size={14} />
      </button>
      {error && <p role="alert">{t(error.key, error.params)}</p>}
    </li>
  );
}

/** The relations of a node, from the node dialog. */
export function NodeRelations({ node }) {
  const { t } = useTranslation();
  const [version, setVersion] = useState(0);
  const relations = useList(`/api/relations?node_id=${node.id}`, version);
  const nodes = useList("/api/nodes", version);
  const [adding, setAdding] = useState(false);
  const [other, setOther] = useState("");
  const [outgoing, setOutgoing] = useState(true);
  const refresh = () => setVersion((v) => v + 1);
  const peer = nodes.find((n) => n.id === Number(other));
  const self = { nodeId: node.id, nodeName: node.name };
  const them = peer ? { nodeId: peer.id, nodeName: peer.name } : null;

  return (
    <section className="node-relations" data-testid="node-relations">
      <h3 className="section-label">{t("relations.title")}</h3>
      {relations.length === 0 && <p className="composer-note">{t("relations.none")}</p>}
      <ul className="relation-list">
        {relations.map((rel) => <RelationRow key={rel.id} rel={rel} onChanged={refresh} />)}
      </ul>
      {!adding ? (
        nodes.length > 1 && (
          <button type="button" className="link-button" data-testid="relation-add" onClick={() => setAdding(true)}>
            <Link2 size={14} /> {t("relations.add")}
          </button>
        )
      ) : <>
        <select className="session-select" aria-label={t("relations.otherNode")} value={other}
          data-testid="relation-other" onChange={(e) => setOther(e.target.value)}>
          <option value="">{t("relations.otherNode")}</option>
          {nodes.filter((n) => n.id !== node.id).map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
        </select>
        <label className="option-item">
          <input type="checkbox" checked={!outgoing} onChange={(e) => setOutgoing(!e.target.checked)} />
          <span>{t("relations.incoming")}</span>
        </label>
        {them && <RelationForm key={`${them.nodeId}-${outgoing}`} from={outgoing ? self : them} to={outgoing ? them : self}
          onDone={() => { setAdding(false); setOther(""); refresh(); }} onCancel={() => setAdding(false)} />}
      </>}
    </section>
  );
}

/** RFC-07: "Create relation" on an agent-to-agent request card. */
export function CreateRelation({ sender, recipient }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  if (!sender || !recipient) return null;
  if (done) return <p className="composer-note" role="status">{t("relations.created")}</p>;
  const side = (s) => ({ nodeId: s.node_id, nodeName: s.node_name, session: s });
  return <>
    <button type="button" className="link-button" data-testid="create-relation" onClick={() => setOpen(true)}>
      <Link2 size={14} /> {t("relations.createFromRequest")}
    </button>
    {open && createPortal(
      <div className="dialog-backdrop" onClick={() => setOpen(false)}>
        <div className="dialog" role="dialog" aria-modal="true" aria-label={t("relations.createFromRequest")}
          onClick={(e) => e.stopPropagation()}>
          <div className="dialog-header">
            <h2>{t("relations.createFromRequest")}</h2>
            <button type="button" className="icon-button" aria-label={t("picker.close")} onClick={() => setOpen(false)}><X size={18} /></button>
          </div>
          <div className="dialog-body">
            <p>{t("relations.explanation")}</p>
            <RelationForm from={side(sender)} to={side(recipient)}
              onDone={() => { setOpen(false); setDone(true); }} onCancel={() => setOpen(false)} />
          </div>
        </div>
      </div>, document.body)}
  </>;
}
