import React, { useCallback, useEffect, useState } from "react";
import { loadDraft, saveDraft } from "./formDrafts";
import { ReplySelect, ReplyToggle } from "./ReplyPolicy";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  ArrowRight,
  Circle,
  Edit2,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { apiErrorDescriptor } from "./i18n/errors";
import { formatDateTime } from "./i18n/formatters";
import { sessionStatus } from "./sessionInfo";
import RelationGraph from "./RelationGraph";

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    const err = new Error(data?.detail || "Request failed");
    err.data = data;
    err.status = res.status;
    err.display = apiErrorDescriptor(data, res.status);
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

/** Form modal to create or edit a project. */
function ProjectFormDialog({ project, onSave, onCancel }) {
  const { t } = useTranslation();
  const [name, setName] = useState(project ? project.name : "");
  const [description, setDescription] = useState(project ? project.description : "");
  const [rule, setRule] = useState(project?.relation_rule || "");
  const [hops, setHops] = useState(project?.max_auto_hops != null ? String(project.max_auto_hops) : "");
  const [rate, setRate] = useState(project?.max_auto_per_hour != null ? String(project.max_auto_per_hour) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const valid = name.trim().length > 0;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSave({
        name: name.trim(),
        description: description.trim(),
        relation_rule: rule.trim() || null,
        max_auto_hops: hops.trim() ? Number(hops) : null,
        max_auto_per_hour: rate.trim() ? Number(rate) : null,
      });
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="dialog-backdrop" onClick={onCancel}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        data-testid="project-form-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-header">
          <h2>{project ? t("projects.editProject") : t("projects.newProject")}</h2>
          <button type="button" className="icon-button" aria-label={t("projects.cancel")} onClick={onCancel}>
            <X size={18} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="dialog-body">
          <label className="prompt-editor-label" htmlFor="project-name">
            {t("projects.name")} *
          </label>
          <input
            id="project-name"
            data-testid="project-name-input"
            className="session-select"
            maxLength={60}
            required
            placeholder={t("projects.namePlaceholder")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />

          <label className="prompt-editor-label" htmlFor="project-desc">
            {t("projects.description")}
          </label>
          <textarea
            id="project-desc"
            data-testid="project-desc-input"
            className="prompt-textarea relation-description"
            maxLength={500}
            placeholder={t("projects.descriptionPlaceholder")}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />

          <label className="prompt-editor-label" htmlFor="project-rule">
            {t("projects.rule")}
          </label>
          <p className="composer-note">{t("projects.ruleHint")}</p>
          <textarea
            id="project-rule"
            data-testid="project-rule-input"
            className="prompt-textarea relation-description"
            maxLength={4000}
            placeholder={t("projects.rulePlaceholder")}
            value={rule}
            onChange={(e) => setRule(e.target.value)}
          />

          <h3 className="section-label">{t("projects.limits")}</h3>
          <p className="composer-note">{t("projects.limitsHint")}</p>
          <div className="project-form-limits">
            <label className="project-form-limit-field">
              <span className="prompt-editor-label">{t("projects.maxHops")}</span>
              <input
                type="number"
                min={1}
                max={20}
                data-testid="project-hops-input"
                className="session-select"
                placeholder="es. 3"
                value={hops}
                onChange={(e) => setHops(e.target.value)}
              />
            </label>
            <label className="project-form-limit-field">
              <span className="prompt-editor-label">{t("projects.maxPerHour")}</span>
              <input
                type="number"
                min={1}
                max={200}
                data-testid="project-rate-input"
                className="session-select"
                placeholder="es. 20"
                value={rate}
                onChange={(e) => setRate(e.target.value)}
              />
            </label>
          </div>

          <div className="dialog-actions">
            <button
              type="submit"
              className="btn btn-primary"
              data-testid="project-save-btn"
              disabled={!valid || busy}
            >
              {t("projects.save")}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid="project-cancel-btn"
              onClick={onCancel}
              disabled={busy}
            >
              {t("projects.cancel")}
            </button>
          </div>
          {error && <p role="alert" className="error-message dialog-error-msg">{t(error.key, error.params)}</p>}
        </form>
      </div>
    </div>,
    document.body
  );
}

/** Relation Type Form modal to create or edit a relation type. */
function RelationTypeDialog({ type, onSave, onCancel }) {
  const { t } = useTranslation();
  const [name, setName] = useState(type ? type.name : "");
  const [description, setDescription] = useState(type ? type.description : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const valid = name.trim().length > 0;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSave({ name: name.trim(), description: description.trim() });
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="dialog-backdrop" onClick={onCancel}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        data-testid="relation-type-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-header">
          <h2>{type ? t("projects.editType") : t("projects.newType")}</h2>
          <button type="button" className="icon-button" aria-label={t("projects.cancel")} onClick={onCancel}>
            <X size={18} />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="dialog-body">
          <label className="prompt-editor-label" htmlFor="type-name">
            {t("relations.typeName")} *
          </label>
          <input
            id="type-name"
            data-testid="relation-type-name-input"
            className="session-select"
            maxLength={40}
            required
            placeholder="es. review"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />

          <label className="prompt-editor-label" htmlFor="type-desc">
            {t("relations.typeDescription")}
          </label>
          <textarea
            id="type-desc"
            data-testid="relation-type-desc-input"
            className="prompt-textarea relation-description"
            maxLength={500}
            placeholder={t("relations.typeDescription")}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />

          <div className="dialog-actions">
            <button
              type="submit"
              className="btn btn-primary"
              data-testid="relation-type-save-btn"
              disabled={!valid || busy}
            >
              {t("projects.saveType")}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid="relation-type-cancel-btn"
              onClick={onCancel}
              disabled={busy}
            >
              {t("projects.cancel")}
            </button>
          </div>
          {error && <p role="alert" className="error-message dialog-error-msg">{t(error.key, error.params)}</p>}
        </form>
      </div>
    </div>,
    document.body
  );
}

/** Relation Types management sub-section. */
function RelationTypesSection() {
  const { t } = useTranslation();
  const [types, setTypes] = useState([]);
  const [editingType, setEditingType] = useState(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    api("GET", "/api/relation-types")
      .then(setTypes)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async (data) => {
    if (editingType) {
      await api("PATCH", `/api/relation-types/${editingType.id}`, data);
      setEditingType(null);
    } else {
      await api("POST", "/api/relation-types", data);
      setCreating(false);
    }
    load();
  };

  const handleDelete = async (ty) => {
    if (!window.confirm(t("projects.deleteTypeConfirm", { name: ty.name }))) return;
    setError(null);
    try {
      await api("DELETE", `/api/relation-types/${ty.id}`);
      load();
    } catch (err) {
      setError(err.display || { key: "errors.relationTypeInUse" });
    }
  };

  return (
    <section className="relation-types-section" data-testid="relation-types-section">
      <div className="relation-types-header">
        <div>
          <h2 className="relation-types-title">{t("projects.relationTypesTitle")}</h2>
          <p className="composer-note relation-types-subtitle">{t("projects.relationTypesSubtitle")}</p>
        </div>
        <button
          type="button"
          className="btn btn-secondary"
          data-testid="new-type-btn"
          onClick={() => setCreating(true)}
        >
          <Plus size={14} /> {t("projects.newType")}
        </button>
      </div>

      {error && <p role="alert" className="error-message relation-types-error">{t(error.key, error.params)}</p>}

      {types.length === 0 ? (
        <p className="composer-note">{t("projects.typesEmpty")}</p>
      ) : (
        <div className="relation-types-list">
          {types.map((ty) => (
            <div key={ty.id} className="card relation-type-card">
              <div className="relation-type-card-header">
                <strong>{ty.name}</strong>
                <div className="relation-type-actions">
                  <button
                    type="button"
                    className="icon-button"
                    data-testid={`edit-type-${ty.id}`}
                    aria-label={t("projects.editType")}
                    onClick={() => setEditingType(ty)}
                  >
                    <Edit2 size={14} />
                  </button>
                  <button
                    type="button"
                    className="icon-button"
                    data-testid={`delete-type-${ty.id}`}
                    aria-label={t("projects.deleteType")}
                    onClick={() => handleDelete(ty)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              {ty.description && <p className="relation-type-desc">{ty.description}</p>}
            </div>
          ))}
        </div>
      )}

      {(creating || editingType) && (
        <RelationTypeDialog
          type={editingType}
          onSave={handleSave}
          onCancel={() => {
            setCreating(false);
            setEditingType(null);
          }}
        />
      )}
    </section>
  );
}

/** Main Projects list view. */
export function ProjectsView({ onOpenProject }) {
  const { t } = useTranslation();
  const [projects, setProjects] = useState([]);
  const [creating, setCreating] = useState(false);
  const [editingProject, setEditingProject] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    api("GET", "/api/projects")
      .then(setProjects)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async (data) => {
    if (editingProject) {
      await api("PATCH", `/api/projects/${editingProject.id}`, data);
      setEditingProject(null);
    } else {
      await api("POST", "/api/projects", data);
      setCreating(false);
    }
    load();
  };

  const handleDelete = async (p) => {
    if (!window.confirm(t("projects.deleteConfirm", { name: p.name }))) return;
    setError(null);
    try {
      await api("DELETE", `/api/projects/${p.id}`);
      load();
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    }
  };

  return (
    <div className="projects-view" data-testid="projects-view">
      <div className="projects-header">
        <div>
          <h1 className="projects-title">{t("projects.title")}</h1>
          <p className="brand-tagline projects-subtitle">{t("projects.subtitle")}</p>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          data-testid="new-project-btn"
          onClick={() => setCreating(true)}
        >
          <Plus size={16} /> {t("projects.newProject")}
        </button>
      </div>

      {error && <p role="alert" className="error-message projects-error">{t(error.key, error.params)}</p>}

      {projects.length === 0 ? (
        <div className="empty-state" data-testid="empty-projects">
          <p>{t("projects.empty")}</p>
        </div>
      ) : (
        <div className="projects-grid">
          {projects.map((p) => (
            <div key={p.id} className="card project-card" data-testid={`project-card-${p.id}`}
              onClick={() => onOpenProject(p.id)}>
              <div className="card-header project-card-header">
                <div>
                  <button
                    type="button"
                    className="link-button project-card-name-btn"
                    data-testid={`open-project-${p.id}`}
                    onClick={(e) => { e.stopPropagation(); onOpenProject(p.id); }}
                  >
                    {p.name}
                  </button>
                  {p.description && (
                    <p className="project-card-desc">
                      {p.description}
                    </p>
                  )}
                </div>
                <div className="project-card-actions">
                  <button
                    type="button"
                    className="icon-button"
                    data-testid={`edit-project-${p.id}`}
                    aria-label={t("projects.editProject")}
                    onClick={(e) => { e.stopPropagation(); setEditingProject(p); }}
                  >
                    <Edit2 size={15} />
                  </button>
                  <button
                    type="button"
                    className="icon-button"
                    data-testid={`delete-project-${p.id}`}
                    aria-label={t("projects.delete")}
                    onClick={(e) => { e.stopPropagation(); handleDelete(p); }}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>

              <div className="project-card-badges">
                <span className="badge" data-testid={`project-nodes-count-${p.id}`}>
                  {t("projects.nodesCount", { count: p.nodes_count })}
                </span>
                <span className="badge" data-testid={`project-relations-count-${p.id}`}>
                  {t("projects.relationsCount", { count: p.relations_count })}
                </span>
                {p.automatic_relations_count > 0 && (
                  <span className="badge badge-auto">
                    {t("projects.autoBadge", { count: p.automatic_relations_count })}
                  </span>
                )}
              </div>

              <div className="project-card-footer">
                {t("projects.created", { date: formatDateTime(p.created_at) })}
              </div>
            </div>
          ))}
        </div>
      )}

      {(creating || editingProject) && (
        <ProjectFormDialog
          project={editingProject}
          onSave={handleSave}
          onCancel={() => {
            setCreating(false);
            setEditingProject(null);
          }}
        />
      )}

      <RelationTypesSection />
    </div>
  );
}

/** Create Relation modal inside project detail view. */
function CreateProjectRelationDialog({ projectId, nodes, types, onDone, onCancel }) {
  const { t } = useTranslation();
  const [fromNode, setFromNode] = useState("");
  const [toNode, setToNode] = useState("");
  const [typeId, setTypeId] = useState("");
  const [approval, setApproval] = useState("manual");
  const [reply, setReply] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const valid = fromNode && toNode && fromNode !== toNode && typeId;

  const handleSave = async (e) => {
    e.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      let createdRelId = null;
      try {
        const res = await api("POST", "/api/relations", {
          from_node: Number(fromNode),
          to_node: Number(toNode),
          type_id: Number(typeId),
          approval,
          reply_approval: reply ?? approval,
        });
        createdRelId = res.id;
      } catch (err) {
        if (err.data?.code === "relation.exists") {
          // Find existing relation
          const allRels = await api("GET", "/api/relations");
          const existing = allRels.find(
            (r) => r.from_node === Number(fromNode) && r.to_node === Number(toNode) && (r.type_id === Number(typeId) || !r.type_id)
          );
          if (existing && window.confirm(t("projects.relationExistsPrompt"))) {
            await api("PUT", `/api/projects/${projectId}/relations/${existing.id}`);
            onDone();
            return;
          }
          setError(err.display || { key: "errors.relationExists" });
          return;
        }
        throw err;
      }

      if (createdRelId) {
        await api("PUT", `/api/projects/${projectId}/relations/${createdRelId}`);
        onDone();
      }
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="dialog-backdrop" onClick={onCancel}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        data-testid="relation-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-header">
          <h2>{t("projects.newRelation")}</h2>
          <button type="button" className="icon-button" aria-label={t("projects.cancel")} onClick={onCancel}>
            <X size={18} />
          </button>
        </div>
        <form onSubmit={handleSave} className="dialog-body">
          <label className="prompt-editor-label" htmlFor="rel-from">
            {t("relations.from")}
          </label>
          <select
            id="rel-from"
            data-testid="new-rel-from-select"
            className="session-select"
            value={fromNode}
            onChange={(e) => setFromNode(e.target.value)}
          >
            <option value="">{t("relations.otherNode")}</option>
            {nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </select>

          <label className="prompt-editor-label" htmlFor="rel-to">
            {t("relations.to")}
          </label>
          <select
            id="rel-to"
            data-testid="new-rel-to-select"
            className="session-select"
            value={toNode}
            onChange={(e) => setToNode(e.target.value)}
          >
            <option value="">{t("relations.otherNode")}</option>
            {nodes
              .filter((n) => String(n.id) !== fromNode)
              .map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name}
                </option>
              ))}
          </select>

          <label className="prompt-editor-label" htmlFor="rel-type">
            {t("relations.type")}
          </label>
          <select
            id="rel-type"
            data-testid="new-rel-type-select"
            className="session-select"
            value={typeId}
            onChange={(e) => setTypeId(e.target.value)}
          >
            <option value="">{t("relations.chooseType")}</option>
            {types.map((ty) => (
              <option key={ty.id} value={ty.id}>
                {ty.name}
              </option>
            ))}
          </select>

          <h3 className="section-label">{t("relations.policy")}</h3>
          <label className="option-item">
            <input
              type="radio"
              name="project-rel-approval"
              checked={approval === "manual"}
              onChange={() => setApproval("manual")}
            />
            <span>{t("relations.manual")}</span>
          </label>
          <label className="option-item">
            <input
              type="radio"
              name="project-rel-approval"
              checked={approval === "automatic"}
              onChange={() => setApproval("automatic")}
            />
            <span>{t("relations.automatic")}</span>
          </label>
          <ReplyToggle approval={approval} reply={reply} onChange={setReply} name="project-rel-reply" />

          <div className="dialog-actions">
            <button
              type="submit"
              className="btn btn-primary"
              data-testid="new-rel-save-btn"
              disabled={!valid || busy}
            >
              {t("relations.create")}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onCancel}
              disabled={busy}
            >
              {t("projects.cancel")}
            </button>
          </div>
          {error && <p role="alert" className="error-message dialog-error-msg">{t(error.key, error.params)}</p>}
        </form>
      </div>
    </div>,
    document.body
  );
}

/** Project Detail View: `#progetti/{id}`. */
/** Project Detail View: `#progetti/{id}`. */
export function ProjectDetailView({ projectId, sessions: propSessions, requests = [], onOpenSession, onBack }) {
  const { t } = useTranslation();
  const [project, setProject] = useState(null);
  const [allNodes, setAllNodes] = useState([]);
  const [allRelations, setAllRelations] = useState([]);
  const [allTypes, setAllTypes] = useState([]);
  const [fetchedSessions, setFetchedSessions] = useState([]);
  const [selectedNodeToAdd, setSelectedNodeToAdd] = useState("");
  const [selectedRelToAdd, setSelectedRelToAdd] = useState("");
  const [editingProject, setEditingProject] = useState(false);
  const [creatingRelation, setCreatingRelation] = useState(false);
  const [error, setError] = useState(null);
  // Unsaved instructions are drafts: the live refresh below reloads the
  // project often, and must never overwrite what the user is typing.
  const projDraftKey = `project:${projectId}:instructions`;
  const nodeDraftKey = `project:${projectId}:nodes`;
  const [projDraft, setProjDraftState] = useState(() => loadDraft(projDraftKey));
  const [nodeDrafts, setNodeDraftsState] = useState(() => loadDraft(nodeDraftKey) || {});
  const setProjDraft = (value) => {
    setProjDraftState(value);
    saveDraft(projDraftKey, value);
  };
  const setNodeDraft = (nodeId, value) => {
    setNodeDraftsState((prev) => {
      const next = { ...prev };
      if (value == null) delete next[nodeId];
      else next[nodeId] = value;
      saveDraft(nodeDraftKey, Object.keys(next).length ? next : null);
      return next;
    });
  };

  const sessions = propSessions || fetchedSessions;

  const load = useCallback(() => {
    let cancelled = false;
    api("GET", `/api/projects/${projectId}`)
      .then((p) => {
        if (!cancelled) {
          setProject(p);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.display || { key: "errors.notFound" });
      });
    Promise.all([
      api("GET", "/api/nodes"),
      api("GET", "/api/relations"),
      api("GET", "/api/relation-types"),
    ])
      .then(([nodes, rels, types]) => {
        if (!cancelled) {
          setAllNodes(nodes);
          setAllRelations(rels);
          setAllTypes(types);
        }
      })
      .catch(() => {});
    if (!propSessions) {
      api("GET", "/api/sessions")
        .then((sList) => {
          if (!cancelled) setFetchedSessions(sList);
        })
        .catch(() => {});
    }
    return () => {
      cancelled = true;
    };
  }, [projectId, propSessions]);

  useEffect(() => {
    return load();
  }, [load]);

  if (!project) {
    return (
      <div className="projects-detail-view" data-testid="project-detail-view">
        <button type="button" className="link-button" data-testid="project-detail-back" onClick={onBack}>
          <ArrowLeft size={16} /> {t("projects.back")}
        </button>
        {error ? <p role="alert">{t(error.key, error.params)}</p> : <p>Loading…</p>}
      </div>
    );
  }

  const projInstructions = projDraft ?? (project.instructions || "");
  const nodeText = (nodeId) => nodeDrafts[nodeId] ?? (project.nodes.find((n) => n.id === nodeId)?.instructions || "");
  const projectNodeIds = new Set(project.nodes.map((n) => n.id));
  const projectRelIds = new Set(project.relations.map((r) => r.id));
  const availableNodes = allNodes.filter((n) => !projectNodeIds.has(n.id));
  const availableRelations = allRelations.filter((r) => !projectRelIds.has(r.id));

  const handleAddNode = async () => {
    if (!selectedNodeToAdd) return;
    try {
      await api("PUT", `/api/projects/${projectId}/nodes/${selectedNodeToAdd}`);
      setSelectedNodeToAdd("");
      load();
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    }
  };

  const handleRemoveNode = async (n) => {
    if (!window.confirm(t("projects.removeNodeConfirm", { name: n.name }))) return;
    try {
      await api("DELETE", `/api/projects/${projectId}/nodes/${n.id}`);
      load();
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    }
  };

  const handleAddRelation = async () => {
    if (!selectedRelToAdd) return;
    try {
      await api("PUT", `/api/projects/${projectId}/relations/${selectedRelToAdd}`);
      setSelectedRelToAdd("");
      load();
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    }
  };

  const handleRemoveRelation = async (rel) => {
    if (!window.confirm(t("projects.removeRelationConfirm"))) return;
    try {
      await api("DELETE", `/api/projects/${projectId}/relations/${rel.id}`);
      load();
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    }
  };

  const handleUpdateRelationApproval = async (relId, approval) => {
    await handleUpdateRelation(relId, { approval });
  };

  const handleUpdateRelation = async (relId, body) => {
    try {
      await api("PATCH", `/api/relations/${relId}`, body);
      load();
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    }
  };

  const handleSaveProjectInstructions = async () => {
    try {
      await api("PATCH", `/api/projects/${projectId}`, {
        instructions: projInstructions.trim() || null,
      });
      setProjDraft(null);
      load();
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    }
  };

  const handleSaveNodeInstructions = async (nodeId) => {
    try {
      const val = (nodeText(nodeId) || "").trim() || null;
      await api("PATCH", `/api/projects/${projectId}/nodes/${nodeId}`, {
        instructions: val,
      });
      setNodeDraft(nodeId, null);
      load();
    } catch (err) {
      setError(err.display || { key: "errors.operation", params: { status: err.status || 500 } });
    }
  };

  const handleUpdateProject = async (data) => {
    await api("PATCH", `/api/projects/${projectId}`, data);
    setEditingProject(false);
    load();
  };

  return (
    <div className="project-detail-view" data-testid="project-detail-view">
      <div className="project-detail-layout">
      <div className="project-detail-main">
      <div className="project-detail-nav">
        <button
          type="button"
          className="link-button project-detail-back-btn"
          data-testid="project-detail-back"
          onClick={onBack}
        >
          <ArrowLeft size={16} /> {t("projects.back")}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          data-testid="edit-project-settings-btn"
          onClick={() => setEditingProject(true)}
        >
          <Edit2 size={14} /> {t("projects.editProject")}
        </button>
      </div>

      <div className="card project-detail-card">
        <h1 className="project-detail-name">{project.name}</h1>
        {project.description && (
          <p className="project-detail-desc">
            {project.description}
          </p>
        )}

        <div className="project-effective-policy-box">
          <h3 className="section-label project-effective-policy-title">{t("projects.effectivePolicy")}</h3>
          <p className="project-effective-limits">
            <strong>{t("projects.effectiveLimitsTitle")}</strong>{" "}
            <span>
              {t("projects.effectiveHops", { hops: project.effective_max_hops })}
              {project.max_auto_hops == null && <small className="project-inherited-hint"> {t("projects.inheritedGlobal")}</small>}
            </span>
            {", "}
            <span>
              {t("projects.effectiveRate", { perHour: project.effective_max_per_hour })}
              {project.max_auto_per_hour == null && <small className="project-inherited-hint"> {t("projects.inheritedGlobal")}</small>}
            </span>
          </p>
          <div className="project-effective-rule">
            <strong>{t("projects.effectiveRule")}</strong>
            {project.effective_rule ? (
              <div className="project-effective-rule-text">{project.effective_rule}</div>
            ) : (
              <span className="project-effective-rule-empty"> {t("relations.ruleEmpty")}</span>
            )}
          </div>
        </div>

        <div className="project-instructions-box" data-testid="project-instructions-box">
          <div className="project-instructions-header">
            <h3 className="section-label">{t("projects.instructionsTitle")}</h3>
            <small className="composer-note">{t("projects.instructionsHint")}</small>
          </div>
          <textarea
            data-testid="project-instructions-input"
            className="prompt-textarea"
            maxLength={4000}
            placeholder={t("projects.instructionsPlaceholder")}
            value={projInstructions}
            onChange={(e) => setProjDraft(e.target.value)}
            rows={3}
          />
          <div className="project-instructions-actions">
            <span className="char-counter">{projInstructions.length} / 4000</span>
            <button
              type="button"
              className={`btn ${projInstructions.trim() !== (project.instructions || "") ? "btn-primary" : "btn-secondary"}`}
              disabled={projInstructions.trim() === (project.instructions || "")}
              data-testid="save-project-instructions-btn"
              onClick={handleSaveProjectInstructions}
            >
              {t("projects.saveInstructions")}
            </button>
          </div>
        </div>
      </div>

      {error && <p role="alert" className="error-message projects-error">{t(error.key, error.params)}</p>}

      {/* Nodes Section */}
      <section className="card project-section-card">
        <h2 className="project-section-title">{t("projects.nodesSection")}</h2>
        {project.nodes.length === 0 ? (
          <p className="composer-note">{t("projects.nodesEmpty")}</p>
        ) : (
          <ul className="project-nodes-list">
            {project.nodes.map((n) => {
              const session = n.session_id ? sessions.find((s) => s.id === n.session_id) : null;
              const status = session ? sessionStatus(session) : null;
              return (
                <li
                  key={n.id}
                  className="project-node-item"
                  data-testid={`project-node-${n.id}`}
                >
                  <div className="project-node-header">
                    {session ? (
                      <button
                        type="button"
                        className="link-button project-node-link"
                        data-testid={`open-node-session-${n.id}`}
                        onClick={() => onOpenSession ? onOpenSession(n.session_id, projectId) : (window.location.hash = `#/sessione/${n.session_id}?project=${projectId}`)}
                      >
                        <strong className="project-node-name">{n.name}</strong>
                        <span className={`session-state state-${status.key}`}>
                          <Circle size={7} fill="currentColor" />
                          {status.label}
                        </span>
                      </button>
                    ) : (
                      <div className="project-node-content">
                        <strong className="project-node-name">{n.name}</strong>
                        <span className="project-node-no-session">{t("projects.noSession")}</span>
                      </div>
                    )}
                    <button
                      type="button"
                      className="icon-button"
                      data-testid={`remove-node-${n.id}`}
                      aria-label={t("projects.removeNode")}
                      title={t("projects.removeNode")}
                      onClick={() => handleRemoveNode(n)}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                  <div className="project-node-instructions">
                    <label className="prompt-editor-label" htmlFor={`node-inst-${n.id}`}>
                      {t("projects.nodeInstructionsTitle")}
                    </label>
                    <textarea
                      id={`node-inst-${n.id}`}
                      data-testid={`node-instructions-input-${n.id}`}
                      className="prompt-textarea"
                      maxLength={4000}
                      placeholder={t("projects.nodeInstructionsPlaceholder")}
                      value={nodeText(n.id)}
                      onChange={(e) => setNodeDraft(n.id, e.target.value)}
                      rows={2}
                    />
                    <div className="project-instructions-actions">
                      <span className="char-counter">{nodeText(n.id).length} / 4000</span>
                      <button
                        type="button"
                        className={`btn ${nodeText(n.id).trim() !== (n.instructions || "") ? "btn-primary" : "btn-secondary"}`}
                        disabled={nodeText(n.id).trim() === (n.instructions || "")}
                        data-testid={`save-node-instructions-btn-${n.id}`}
                        onClick={() => handleSaveNodeInstructions(n.id)}
                      >
                        {t("projects.saveNodeInstructions")}
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        {availableNodes.length > 0 && (
          <div className="project-add-row">
            <select
              className="session-select project-add-node-select"
              data-testid="add-node-select"
              value={selectedNodeToAdd}
              onChange={(e) => setSelectedNodeToAdd(e.target.value)}
            >
              <option value="">{t("projects.chooseNode")}</option>
              {availableNodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid="add-node-btn"
              disabled={!selectedNodeToAdd}
              onClick={handleAddNode}
            >
              <Plus size={14} /> {t("projects.addNode")}
            </button>
          </div>
        )}
      </section>

      {/* Relations Section */}
      <section className="card">
        <div className="project-relations-header">
          <h2 className="project-relations-title">{t("projects.relationsSection")}</h2>
          <button
            type="button"
            className="btn btn-primary"
            data-testid="open-new-relation-btn"
            onClick={() => setCreatingRelation(true)}
          >
            <Plus size={14} /> {t("projects.newRelation")}
          </button>
        </div>

        {project.relations.length === 0 ? (
          <p className="composer-note">{t("projects.relationsEmpty")}</p>
        ) : (
          <ul className="relation-list project-relations-list">
            {project.relations.map((rel) => (
              <li
                key={rel.id}
                className="relation-row project-relation-item"
                data-testid={`project-relation-${rel.id}`}
              >
                <div className="relation-route">
                  <span className="relation-route-text">
                    {rel.from_name} <ArrowRight size={12} aria-hidden="true" /> {rel.to_name}{" "}
                    <small>({rel.type_name})</small>
                  </span>
                  {rel.shared_with && rel.shared_with.length > 0 && (
                    <span
                      className="badge badge-shared"
                      data-testid={`relation-badge-shared-${rel.id}`}
                    >
                      {t("projects.sharedWith", { projects: rel.shared_with.join(", ") })}
                    </span>
                  )}
                </div>
                <div className="project-relation-actions">
                  <select
                    className="session-select relation-approval-select"
                    aria-label={t("relations.policy")}
                    value={rel.approval}
                    data-testid={`relation-approval-${rel.id}`}
                    onChange={(e) => handleUpdateRelationApproval(rel.id, e.target.value)}
                  >
                    <option value="manual">{t("relations.manualShort")}</option>
                    <option value="automatic">{t("relations.automaticShort")}</option>
                  </select>
                  <ReplySelect rel={rel} onChange={(value) => handleUpdateRelation(rel.id, { reply_approval: value })} />
                  <button
                    type="button"
                    className="icon-button"
                    data-testid={`remove-relation-${rel.id}`}
                    aria-label={t("projects.removeRelation")}
                    title={t("projects.removeRelation")}
                    onClick={() => handleRemoveRelation(rel)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {availableRelations.length > 0 && (
          <div className="project-add-relation-row">
            <select
              className="session-select project-add-relation-select"
              data-testid="add-relation-select"
              value={selectedRelToAdd}
              onChange={(e) => setSelectedRelToAdd(e.target.value)}
            >
              <option value="">{t("projects.chooseRelation")}</option>
              {availableRelations.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.from_name} → {r.to_name} ({r.type_name})
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid="add-relation-btn"
              disabled={!selectedRelToAdd}
              onClick={handleAddRelation}
            >
              <Plus size={14} /> {t("projects.addRelation")}
            </button>
          </div>
        )}
      </section>
      </div>
      {/* RF-44: beside the project in landscape, at the bottom in portrait. */}
      <aside className="card project-graph-panel" aria-label={t("projects.graphTitle")}>
        <h2 className="project-relations-title">{t("projects.graphTitle")}</h2>
        <RelationGraph nodes={project.nodes} relations={project.relations} sessions={sessions} requests={requests}
          projectId={projectId} onOpenSession={onOpenSession} />
      </aside>
      </div>

      {editingProject && (
        <ProjectFormDialog
          project={project}
          onSave={handleUpdateProject}
          onCancel={() => setEditingProject(false)}
        />
      )}

      {creatingRelation && (
        <CreateProjectRelationDialog
          projectId={projectId}
          nodes={allNodes}
          types={allTypes}
          onDone={() => {
            setCreatingRelation(false);
            load();
          }}
          onCancel={() => setCreatingRelation(false)}
        />
      )}
    </div>
  );
}
