import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { providerOf } from "./sessionInfo";

async function call(url, method = "GET", body) {
  const res = await fetch(url, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/**
 * Near a provider's usage limit, its automatic relations (both directions)
 * are reviewed by the user until usage goes down. Claude's usage is known only
 * while its login is valid, hence the automatic renewal next to it.
 */
export default function UsageGuard() {
  const { t } = useTranslation();
  const [saved, setSaved] = useState(null);
  const [form, setForm] = useState(null);
  const [renew, setRenew] = useState(null);
  const [deliverAtReset, setDeliverAtReset] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    call("/api/settings/usage-guard").then((g) => { setSaved(g); setForm(g); }).catch(() => setError(true));
    call("/api/settings/claude-renew").then((r) => setRenew(r.enabled)).catch(() => {});
    call("/api/settings/deliver-at-reset").then((d) => setDeliverAtReset(d.enabled)).catch(() => {});
  }, []);

  if (!form) return error ? <p className="composer-note" role="alert">{t("settings.loadError")}</p> : null;

  const valid = [form.five_hour, form.seven_day].every((v) => Number.isInteger(v) && v >= 50 && v <= 100);
  const changed = ["enabled", "five_hour", "seven_day"].some((k) => form[k] !== saved[k]);
  const save = async () => {
    setError(false);
    try {
      const out = await call("/api/settings/usage-guard", "PUT",
        { enabled: form.enabled, five_hour: form.five_hour, seven_day: form.seven_day });
      setSaved(out); setForm(out);
    } catch { setError(true); }
  };
  const toggleRenew = async () => {
    const next = !renew;
    setRenew(next);
    try { setRenew((await call("/api/settings/claude-renew", "PUT", { enabled: next })).enabled); }
    catch { setRenew(!next); }
  };
  const toggleDeliverAtReset = async () => {
    const next = !deliverAtReset;
    setDeliverAtReset(next);
    try { setDeliverAtReset((await call("/api/settings/deliver-at-reset", "PUT", { enabled: next })).enabled); }
    catch { setDeliverAtReset(!next); }
  };
  const percent = (key) => (e) => setForm({ ...form, [key]: e.target.value === "" ? "" : Number(e.target.value) });

  return (
    <div className="usage-guard" data-testid="usage-guard">
      <label className="settings-row settings-check">
        <span className="settings-row-text">
          <strong>{t("settings.guardTitle")}</strong>
          <small>{t("settings.guardHint")}</small>
        </span>
        <input type="checkbox" data-testid="usage-guard-toggle" checked={form.enabled}
          onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
      </label>
      <div className="usage-guard-thresholds">
        <label>
          {t("settings.guard5h")}
          <span className="percent-input">
            <input type="number" min={50} max={100} step={1} data-testid="usage-guard-5h"
              value={form.five_hour} onChange={percent("five_hour")} />%
          </span>
        </label>
        <label>
          {t("settings.guard7d")}
          <span className="percent-input">
            <input type="number" min={50} max={100} step={1} data-testid="usage-guard-7d"
              value={form.seven_day} onChange={percent("seven_day")} />%
          </span>
        </label>
        <button type="button" className={`btn ${changed ? "btn-primary" : "btn-secondary"}`} data-testid="usage-guard-save"
          disabled={!changed || !valid} onClick={save}>
          {t("settings.save")}
        </button>
      </div>
      {!valid && <p className="composer-note" role="alert">{t("settings.guardRange")}</p>}
      {error && <p className="composer-note" role="alert">{t("settings.saveError")}</p>}
      {saved.enabled && saved.guarded.length > 0 && (
        <p className="usage-guard-active" data-testid="usage-guard-active" role="status">
          {t("settings.guardActive", { providers: saved.guarded.map((p) => providerOf({ provider: p }).label).join(", ") })}
        </p>
      )}
      {saved.enabled && renew === false && (
        <p className="usage-guard-warning" data-testid="usage-guard-claude-warning">{t("settings.guardClaudeWarning")}</p>
      )}
      <label className="settings-row settings-check">
        <span className="settings-row-text">
          <strong>{t("usage.autoRenew")}</strong>
          <small>{t("settings.renewHint")}</small>
        </span>
        <input type="checkbox" data-testid="toggle-claude-auto-renew" checked={!!renew} disabled={renew === null}
          onChange={toggleRenew} />
      </label>
      <label className="settings-row settings-check">
        <span className="settings-row-text">
          <strong>{t("settings.deliverAtResetTitle")}</strong>
          <small>{t("settings.deliverAtResetHint")}</small>
        </span>
        <input type="checkbox" data-testid="deliver-at-reset-toggle" checked={!!deliverAtReset} disabled={deliverAtReset === null}
          onChange={toggleDeliverAtReset} />
      </label>
    </div>
  );
}
