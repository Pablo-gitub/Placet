import React, { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  ArrowRight,
  Edit2,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { apiErrorDescriptor } from "./i18n/errors";
import { formatDateTime } from "./i18n/formatters";

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
          <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", marginBottom: "1rem" }}>
            <label style={{ flex: "1 1 120px" }}>
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
            <label style={{ flex: "1 1 120px" }}>
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
          {error && <p role="alert" className="error-message" style={{ color: "var(--danger)", marginTop: "0.5rem" }}>{t(error.key, error.params)}</p>}
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
          {error && <p role="alert" className="error-message" style={{ color: "var(--danger)", marginTop: "0.5rem" }}>{t(error.key, error.params)}</p>}
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
    <section className="relation-types-section" data-testid="relation-types-section" style={{ marginTop: "2rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem", flexWrap: "wrap", gap: "0.5rem" }}>
        <div>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 700, margin: 0 }}>{t("projects.relationTypesTitle")}</h2>
          <p className="composer-note" style={{ margin: "0.2rem 0 0" }}>{t("projects.relationTypesSubtitle")}</p>
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

      {error && <p role="alert" className="error-message" style={{ color: "var(--danger)", marginBottom: "0.75rem" }}>{t(error.key, error.params)}</p>}

      {types.length === 0 ? (
        <p className="composer-note">{t("projects.typesEmpty")}</p>
      ) : (
        <div className="relation-types-list" style={{ display: "grid", gap: "0.75rem", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
          {types.map((ty) => (
            <div key={ty.id} className="card" style={{ padding: "0.75rem 1rem", margin: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>{ty.name}</strong>
                <div style={{ display: "flex", gap: "0.4rem" }}>
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
              {ty.description && <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", margin: "0.4rem 0 0" }}>{ty.description}</p>}
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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap", gap: "0.75rem" }}>
        <div>
          <h1 style={{ fontSize: "1.3rem", fontWeight: 700, margin: 0 }}>{t("projects.title")}</h1>
          <p className="brand-tagline" style={{ margin: "0.2rem 0 0" }}>{t("projects.subtitle")}</p>
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

      {error && <p role="alert" className="error-message" style={{ color: "var(--danger)", marginBottom: "1rem" }}>{t(error.key, error.params)}</p>}

      {projects.length === 0 ? (
        <div className="empty-state" data-testid="empty-projects">
          <p>{t("projects.empty")}</p>
        </div>
      ) : (
        <div className="projects-grid" style={{ display: "grid", gap: "1rem", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}>
          {projects.map((p) => (
            <div key={p.id} className="card project-card" data-testid={`project-card-${p.id}`} style={{ margin: 0 }}>
              <div className="card-header" style={{ alignItems: "flex-start", marginBottom: "0.5rem" }}>
                <div>
                  <button
                    type="button"
                    className="link-button"
                    data-testid={`open-project-${p.id}`}
                    onClick={() => onOpenProject(p.id)}
                    style={{ fontSize: "1.05rem", fontWeight: 700, padding: 0, textAlign: "left", color: "var(--primary)" }}
                  >
                    {p.name}
                  </button>
                  {p.description && (
                    <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", margin: "0.25rem 0 0" }}>
                      {p.description}
                    </p>
                  )}
                </div>
                <div style={{ display: "flex", gap: "0.3rem" }}>
                  <button
                    type="button"
                    className="icon-button"
                    data-testid={`edit-project-${p.id}`}
                    aria-label={t("projects.editProject")}
                    onClick={() => setEditingProject(p)}
                  >
                    <Edit2 size={15} />
                  </button>
                  <button
                    type="button"
                    className="icon-button"
                    data-testid={`delete-project-${p.id}`}
                    aria-label={t("projects.delete")}
                    onClick={() => handleDelete(p)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>

              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", margin: "0.75rem 0 0.5rem", alignItems: "center" }}>
                <span className="badge" data-testid={`project-nodes-count-${p.id}`}>
                  {t("projects.nodesCount", { count: p.nodes_count })}
                </span>
                <span className="badge" data-testid={`project-relations-count-${p.id}`}>
                  {t("projects.relationsCount", { count: p.relations_count })}
                </span>
                {p.automatic_relations_count > 0 && (
                  <span className="badge" style={{ backgroundColor: "rgba(16, 185, 129, 0.15)", color: "var(--success)" }}>
                    {t("projects.autoBadge", { count: p.automatic_relations_count })}
                  </span>
                )}
              </div>

              <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "auto", borderTop: "1px solid var(--border-color)", paddingTop: "0.5rem" }}>
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
          {error && <p role="alert" className="error-message" style={{ color: "var(--danger)", marginTop: "0.5rem" }}>{t(error.key, error.params)}</p>}
        </form>
      </div>
    </div>,
    document.body
  );
}

/** Project Detail View: `#progetti/{id}`. */
export function ProjectDetailView({ projectId, onBack }) {
  const { t } = useTranslation();
  const [project, setProject] = useState(null);
  const [allNodes, setAllNodes] = useState([]);
  const [allRelations, setAllRelations] = useState([]);
  const [allTypes, setAllTypes] = useState([]);
  const [selectedNodeToAdd, setSelectedNodeToAdd] = useState("");
  const [selectedRelToAdd, setSelectedRelToAdd] = useState("");
  const [editingProject, setEditingProject] = useState(false);
  const [creatingRelation, setCreatingRelation] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    let cancelled = false;
    api("GET", `/api/projects/${projectId}`)
      .then((p) => {
        if (!cancelled) setProject(p);
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
    return () => {
      cancelled = true;
    };
  }, [projectId]);

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

  const handleUpdateProject = async (data) => {
    await api("PATCH", `/api/projects/${projectId}`, data);
    setEditingProject(false);
    load();
  };

  return (
    <div className="project-detail-view" data-testid="project-detail-view">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <button
          type="button"
          className="link-button"
          data-testid="project-detail-back"
          onClick={onBack}
          style={{ display: "flex", alignItems: "center", gap: "0.4rem", padding: 0 }}
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

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h1 style={{ fontSize: "1.4rem", fontWeight: 700, margin: "0 0 0.5rem" }}>{project.name}</h1>
        {project.description && (
          <p style={{ color: "var(--text-muted)", fontSize: "0.9rem", margin: "0 0 1rem" }}>
            {project.description}
          </p>
        )}

        <div style={{ backgroundColor: "var(--bg-secondary)", borderRadius: "8px", padding: "0.75rem 1rem", border: "1px solid var(--border-color)" }}>
          <h3 className="section-label" style={{ marginTop: 0 }}>{t("projects.effectivePolicy")}</h3>
          <p style={{ fontSize: "0.85rem", margin: "0.25rem 0" }}>
            <strong>{t("projects.effectiveLimits", { hops: project.effective_max_hops, perHour: project.effective_max_per_hour })}</strong>
            {project.max_auto_hops == null && <small style={{ color: "var(--text-muted)", marginLeft: "0.4rem" }}>{t("projects.inheritedGlobal")}</small>}
          </p>
          <p style={{ fontSize: "0.85rem", margin: "0.4rem 0 0" }}>
            <strong>{t("projects.effectiveRule")}</strong>{" "}
            {project.effective_rule ? (
              <span style={{ whiteSpace: "pre-wrap", display: "inline-block", verticalAlign: "top" }}>{project.effective_rule}</span>
            ) : (
              <span style={{ color: "var(--text-muted)" }}>{t("relations.ruleEmpty")}</span>
            )}
          </p>
        </div>
      </div>

      {error && <p role="alert" className="error-message" style={{ color: "var(--danger)", marginBottom: "1rem" }}>{t(error.key, error.params)}</p>}

      {/* Nodes Section */}
      <section className="card" style={{ marginBottom: "1.5rem" }}>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 700, margin: "0 0 0.75rem" }}>{t("projects.nodesSection")}</h2>
        {project.nodes.length === 0 ? (
          <p className="composer-note">{t("projects.nodesEmpty")}</p>
        ) : (
          <ul style={{ listStyle: "none", padding: 0, margin: "0 0 1rem", display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
            {project.nodes.map((n) => (
              <li
                key={n.id}
                data-testid={`project-node-${n.id}`}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.5rem",
                  padding: "0.4rem 0.75rem",
                  backgroundColor: "var(--bg-secondary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "6px",
                  fontSize: "0.85rem",
                }}
              >
                <span>{n.name}</span>
                <button
                  type="button"
                  className="icon-button"
                  data-testid={`remove-node-${n.id}`}
                  aria-label={t("projects.removeNode")}
                  onClick={() => handleRemoveNode(n)}
                >
                  <Trash2 size={13} />
                </button>
              </li>
            ))}
          </ul>
        )}

        {availableNodes.length > 0 && (
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", marginTop: "0.5rem" }}>
            <select
              className="session-select"
              data-testid="add-node-select"
              value={selectedNodeToAdd}
              onChange={(e) => setSelectedNodeToAdd(e.target.value)}
              style={{ maxWidth: "250px" }}
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
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem", flexWrap: "wrap", gap: "0.5rem" }}>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 700, margin: 0 }}>{t("projects.relationsSection")}</h2>
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
          <ul className="relation-list" style={{ margin: "0 0 1rem" }}>
            {project.relations.map((rel) => (
              <li
                key={rel.id}
                className="relation-row"
                data-testid={`project-relation-${rel.id}`}
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}
              >
                <div style={{ display: "flex", flexDirection: "column", gap: "0.2rem" }}>
                  <span className="relation-route">
                    {rel.from_name} <ArrowRight size={12} aria-hidden="true" /> {rel.to_name}{" "}
                    <small>({rel.type_name})</small>
                  </span>
                  <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", alignItems: "center" }}>
                    <span className="badge" style={{ fontSize: "0.7rem" }}>
                      {rel.approval === "automatic" ? t("relations.automaticShort") : t("relations.manualShort")}
                    </span>
                    {rel.shared_with && rel.shared_with.length > 0 && (
                      <span
                        className="badge"
                        data-testid={`relation-badge-shared-${rel.id}`}
                        style={{ backgroundColor: "rgba(245, 158, 11, 0.15)", color: "var(--warning)", fontSize: "0.7rem" }}
                      >
                        {t("projects.sharedWith", { projects: rel.shared_with.join(", ") })}
                      </span>
                    )}
                  </div>
                </div>
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
              </li>
            ))}
          </ul>
        )}

        {availableRelations.length > 0 && (
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", borderTop: "1px solid var(--border-color)", paddingTop: "0.75rem" }}>
            <select
              className="session-select"
              data-testid="add-relation-select"
              value={selectedRelToAdd}
              onChange={(e) => setSelectedRelToAdd(e.target.value)}
              style={{ maxWidth: "300px" }}
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
