import React, { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { OctagonX, Play, SlidersHorizontal } from "lucide-react";
import { apiErrorDescriptor } from "./i18n/errors";

async function call(method, url, body) {
  const res = await fetch(url, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw Object.assign(new Error("Automation request failed"), { display: apiErrorDescriptor(data, res.status) });
  }
  return res.json();
}

/**
 * RFC-14: automatic relations at a glance, with Stop all / Resume and the
 * loop limits. Hidden while there is nothing automatic and nothing paused.
 */
export default function AutomationBar({ version = 0 }) {
  const { t } = useTranslation();
  const [state, setState] = useState(null);
  const [editing, setEditing] = useState(false);
  const [hops, setHops] = useState("");
  const [perHour, setPerHour] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    call("GET", "/api/automation").then(setState).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load, version]);

  const run = async (method, url, body) => {
    setBusy(true); setError(null);
    try { setState(await call(method, url, body)); setEditing(false); }
    catch (e) { setError(e.display || { key: "errors.automation" }); }
    finally { setBusy(false); }
  };
  if (!state || (!state.paused && !state.automatic_relations)) return null;

  return (
    <section className={`automation-bar${state.paused ? " paused" : ""}`} data-testid="automation-bar">
      <div className="automation-line">
        <span className="automation-text">
          {state.paused
            ? t("automation.paused")
            : t("automation.active", { count: state.automatic_relations, hops: state.max_hops, perHour: state.max_per_hour })}
        </span>
        <button type="button" className="icon-button" aria-label={t("automation.limits")} title={t("automation.limits")}
          onClick={() => { setHops(String(state.max_hops)); setPerHour(String(state.max_per_hour)); setEditing((v) => !v); }}>
          <SlidersHorizontal size={15} />
        </button>
        {state.paused ? (
          <button type="button" className="btn btn-secondary automation-button" data-testid="automation-resume"
            disabled={busy} onClick={() => run("POST", "/api/automation/resume")}>
            <Play size={14} /> {t("automation.resume")}
          </button>
        ) : (
          <button type="button" className="btn btn-danger automation-button" data-testid="automation-stop"
            disabled={busy} onClick={() => run("POST", "/api/automation/stop")}>
            <OctagonX size={14} /> {t("automation.stop")}
          </button>
        )}
      </div>
      {editing && (
        <div className="automation-limits" data-testid="automation-limits">
          <label>
            {t("automation.maxHops")}
            <input type="number" min={1} max={10} value={hops} data-testid="automation-hops"
              onChange={(e) => setHops(e.target.value)} />
          </label>
          <label>
            {t("automation.maxPerHour")}
            <input type="number" min={1} max={200} value={perHour} data-testid="automation-per-hour"
              onChange={(e) => setPerHour(e.target.value)} />
          </label>
          <button type="button" className="btn btn-primary" disabled={busy} data-testid="automation-save"
            onClick={() => run("PUT", "/api/automation", { max_hops: Number(hops), max_per_hour: Number(perHour) })}>
            {t("automation.save")}
          </button>
        </div>
      )}
      {error && <p role="alert">{t(error.key, error.params)}</p>}
    </section>
  );
}
