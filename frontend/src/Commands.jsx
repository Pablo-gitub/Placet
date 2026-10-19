import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ExternalLink, ShieldCheck } from "lucide-react";
import { apiErrorDescriptor } from "./i18n/errors";

const ACTIONS = new Set(["restart-backend", "restart-bridge"]);
const ID = /^[a-f0-9]{32}$/;
const KEY = "placet.commands.references.v1";
const GUIDE = "https://github.com/Pablo-gitub/Placet/blob/main/docs/installation.md#reserved-administrative-commands";
const STATES = new Set(["awaiting_confirmation", "expired", "authorized", "running", "succeeded", "failed", "outcome_unknown"]);
const TERMINAL = "sudo /usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/usr/local/lib/placet-admin /usr/local/lib/placet-admin-venv/bin/python -s -m admin_executor.terminal";

function loadReferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY));
    const pending = saved?.pending;
    const valid = ACTIONS.has(pending?.action) && /^[A-Za-z0-9_-]{1,128}$/.test(pending?.request_key || "");
    return { pending: valid ? { action: pending.action, request_key: pending.request_key } : null,
      watched: Object.fromEntries(Object.entries(saved?.watched || {}).filter(([action, id]) => ACTIONS.has(action) && ID.test(id))) };
  } catch { return { pending: null, watched: {} }; }
}

/** Only public IDs are cached; confirmation URLs and signing data never are. */
function publicView(row) {
  if (!row || !ID.test(row.id) || !ACTIONS.has(row.action) || !STATES.has(row.state)
      || typeof row.enabled !== "boolean" || !Number.isFinite(row.expires_at)) return null;
  try {
    const url = new URL(row.confirmation_url);
    if (url.protocol !== "https:" || url.username || url.password || url.port || url.pathname !== "/"
        || url.hash || Array.from(url.searchParams).length !== 1 || url.searchParams.get("proposal") !== row.id) return null;
    if ((row.job_id !== null && !ID.test(row.job_id))
        || (["awaiting_confirmation", "expired"].includes(row.state) !== (row.job_id === null))) return null;
    return { id: row.id, action: row.action, state: row.state, enabled: row.enabled,
      expires_at: row.expires_at, job_id: row.job_id, confirmation_url: url.href,
      evidence: row.evidence === "user_service_report" ? "user_service_report" : "system_service_health" };
  } catch { return null; }
}

function TerminalHelp({ id }) {
  const { t } = useTranslation();
  if (!ID.test(id || "")) return null;
  return <details className="commands-terminal">
    <summary>{t("commands.terminalInstructions")}</summary>
    <p>{t("commands.reconcileHelp")}</p>
    <p>{t("commands.reconcileSuccess")}</p>
    <code>{`${TERMINAL} reconcile --id ${id} --outcome succeeded`}</code>
    <p>{t("commands.reconcileFailure")}</p>
    <code>{`${TERMINAL} reconcile --id ${id} --outcome failed`}</code>
  </details>;
}

export default function Commands({ onBack }) {
  const { t } = useTranslation();
  const [initial] = useState(loadReferences);
  const journal = useRef(initial);
  const [pending, setPending] = useState(initial.pending);
  const [watched, setWatched] = useState(initial.watched);
  const [catalogue, setCatalogue] = useState(null);
  const [connection, setConnection] = useState("loading");
  const [views, setViews] = useState({});
  const viewRef = useRef({});
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [feedback, setFeedback] = useState(null);
  const alive = useRef(false);
  const controllers = useRef(new Set());

  async function request(url, body) {
    const controller = new AbortController();
    controllers.current.add(controller);
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(url, { method: body ? "POST" : "GET", cache: "no-store", credentials: "same-origin", redirect: "error",
        signal: controller.signal, headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined });
      if (!response.ok) {
        const error = new Error("Administrative response unavailable");
        error.status = response.status;
        try { error.descriptor = apiErrorDescriptor(await response.json(), response.status); } catch { /* No raw diagnostics. */ }
        throw error;
      }
      return await response.json();
    } finally { clearTimeout(timeout); controllers.current.delete(controller); }
  }

  useEffect(() => {
    let active = true, timer, failures = 0, polling = false;
    const activeControllers = controllers.current;
    alive.current = true;
    async function poll() {
      if (!active || polling) return;
      polling = true; clearTimeout(timer);
      try {
        const data = await request("/api/admin/catalogue");
        if (!active) return;
        if (data.status !== "available") {
          setConnection((previous) => previous === "loading" || previous === "not_configured" ? "not_configured" : "recovering");
          failures += 1;
        } else {
          if (!Array.isArray(data.actions) || typeof data.execution_enabled !== "boolean"
              || (data.blocked_job_id != null && !ID.test(data.blocked_job_id))) throw new Error("Invalid public catalogue");
          const actions = data.actions.filter((item) => ACTIONS.has(item.id) && item.enabled === true);
          const rows = {};
          for (const row of data.recent || []) {
            const valid = publicView(row); if (valid) rows[valid.id] = valid;
          }
          const observed = new Set([
            ...Object.values(journal.current.watched),
            ...Object.values(viewRef.current).filter((row) => ["authorized", "running", "outcome_unknown"].includes(row.state)).map((row) => row.id),
          ]);
          for (const id of Array.from(observed).slice(0, 12)) {
            if (rows[id]) continue;
            try {
              const row = publicView(await request(`/api/admin/proposals/${id}`));
              if (row) rows[id] = row;
            } catch { /* A missing historical reference never hides the action list. */ }
          }
          if (!active) return;
          viewRef.current = { ...viewRef.current, ...rows };
          setViews(viewRef.current);
          setCatalogue({ ...data, actions }); setConnection("online"); failures = 0;
        }
      } catch {
        if (active) { setConnection("recovering"); failures += 1; }
      } finally {
        polling = false;
        if (active) timer = setTimeout(poll, Math.min(30000, 2000 * 2 ** Math.min(failures, 4)));
      }
    }
    function refresh() { if (document.visibilityState !== "hidden") poll(); }
    poll();
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      active = false; alive.current = false; clearTimeout(timer);
      window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh);
      for (const controller of activeControllers) controller.abort();
    };
  }, []);

  function persist(next) {
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
      journal.current = next; setPending(next.pending); setWatched(next.watched); return true;
    } catch { setFeedback({ key: "commands.storageUnavailable" }); return false; }
  }

  async function prepare(action, retry = false) {
    if (submitting.current || connection !== "online" || !catalogue?.actions.some((item) => item.id === action)
        || blocked || (!retry && journal.current.pending)) return;
    let intent = journal.current.pending;
    if (!retry) {
      let key;
      try { key = crypto.randomUUID(); } catch { setFeedback({ key: "commands.storageUnavailable" }); return; }
      intent = { action, request_key: key };
      // Save before sending. If storage is unavailable, no intent is submitted.
      if (!persist({ ...journal.current, pending: intent })) return;
    }
    if (!intent || intent.action !== action) return;
    submitting.current = true; setBusy(true); setFeedback(null);
    try {
      const row = publicView(await request("/api/admin/proposals", { ...intent, parameters: {} }));
      if (!alive.current) return;
      if (!row || row.action !== action) throw new Error("Invalid public proposal");
      viewRef.current = { ...viewRef.current, [row.id]: row }; setViews(viewRef.current);
      persist({ pending: null, watched: { ...journal.current.watched, [action]: row.id } });
    } catch (error) {
      // A lost acknowledgement is not a failed command. Preserve the intent
      // key across reloads; retry only on another explicit human tap.
      if (alive.current) setFeedback(error.status < 500 && error.descriptor
        ? error.descriptor : { key: "commands.creationUnconfirmed" });
    } finally {
      submitting.current = false; if (alive.current) setBusy(false);
    }
  }

  const online = connection === "online";
  const rows = Object.values(views).sort((a, b) => b.expires_at - a.expires_at);
  const blocked = catalogue && Object.hasOwn(catalogue, "blocked_job_id")
    ? catalogue.blocked_job_id : rows.find((row) => row.state === "outcome_unknown")?.job_id;
  const selected = (action) => views[watched[action]] || rows.find((row) => row.action === action);
  const renderStatus = (row) => <>
    <p className="commands-state" role="status">{t(`commands.states.${row.state}`)}</p>
    {row.evidence === "user_service_report" && <p className="commands-hint">{t("commands.bridgeEvidence")}</p>}
  </>;

  return <section className="commands" data-testid="commands-section">
    <button type="button" className="link-button project-detail-back-btn" data-testid="commands-back" onClick={onBack}>
      <ArrowLeft size={16} /> {t("settings.title")}
    </button>
    <h2>{t("commands.title")}</h2>
    <p className="commands-hint">{t("commands.intro")}</p>
    {connection === "loading" && <p role="status">{t("commands.loading")}</p>}
    {connection === "recovering" && <p role="status" className="commands-notice">{t("commands.reconnecting")}</p>}
    {connection === "not_configured" && <p role="status">{t("commands.notConfigured")}</p>}
    {(connection === "not_configured" || (online && !catalogue.actions.length)) && <>
      {online && <p>{t("commands.disabled")}</p>}
      <a href={GUIDE} target="_blank" rel="noopener noreferrer">{t("commands.installationGuide")}</a>
    </>}
    {blocked && <div className="commands-notice" role="status">
      <p>{t("commands.blocked")}</p><TerminalHelp id={blocked} />
    </div>}
    {pending && <div className="commands-notice">
      <p>{t(busy ? "commands.preparing" : "commands.creationUnconfirmed")}</p>
      <button className="btn btn-secondary" type="button" disabled={!online || busy || Boolean(blocked)
          || !catalogue?.actions.some((item) => item.id === pending.action)} onClick={() => prepare(pending.action, true)}>
        {t("commands.retryCreation")}
      </button>
    </div>}
    {feedback && feedback.key !== "commands.creationUnconfirmed"
      && <p role="status" className="commands-notice">{t(feedback.key, feedback.params)}</p>}
    {online && catalogue.actions.map((action) => {
      const row = selected(action.id);
      const awaiting = row?.state === "awaiting_confirmation";
      const inProgress = awaiting || ["authorized", "running", "outcome_unknown"].includes(row?.state);
      const canConfirm = awaiting && row.enabled && !blocked && !pending && !busy;
      return <article className="card commands-card" key={action.id}>
        <h3><ShieldCheck size={18} aria-hidden="true" />{t(`commands.actions.${action.id}.title`)}</h3>
        <p>{t(`commands.actions.${action.id}.description`)}</p>
        {row && renderStatus(row)}
        {!inProgress && <button className="btn btn-primary" type="button" disabled={busy || Boolean(pending) || Boolean(blocked)}
          onClick={() => prepare(action.id)}>{t(row?.state === "expired" ? "commands.newProposal" : "commands.run")}</button>}
        {canConfirm && <div className="commands-confirmation">
          <p>{t("commands.confirmationHost")} <strong>{new URL(row.confirmation_url).host}</strong></p>
          <a className="btn btn-primary" href={row.confirmation_url} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={16} aria-hidden="true" /> {t("commands.confirm")}
          </a>
        </div>}
      </article>;
    })}
    {rows.length > 0 && <details className="commands-history">
      <summary>{t("commands.recent")}</summary>
      {rows.slice(0, 12).map((row) => <div className="commands-history-row" key={row.id}>
        <strong>{t(`commands.actions.${row.action}.title`)}</strong>
        {renderStatus(row)}<small>{t("commands.reference", { id: row.id.slice(0, 8) })}</small>
      </div>)}
    </details>}
  </section>;
}
