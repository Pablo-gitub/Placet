import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { Gauge, RotateCcw } from "lucide-react";
import { formatNumber } from "./i18n/formatters";
import { apiErrorDescriptor } from "./i18n/errors";
import { providerOf, timeAgo } from "./sessionInfo";
import { untilReset, usageRows } from "./usage";

const level = (used) => (used >= 90 ? "high" : used >= 70 ? "mid" : "low");

function Window({ name, value, t }) {
  if (!value) return null;
  if (value.used === null) {
    return (
      <span className="usage-window usage-unknown">
        <span className="usage-name">{name}</span>
        <span className="usage-value">—</span>
      </span>
    );
  }
  const reset = value.resetsAt && value.used > 0 ? untilReset(value.resetsAt, t) : null;
  return (
    <span className={`usage-window usage-${level(value.used)}`}>
      <span className="usage-name">{name}</span>
      <span className="usage-meter" aria-hidden="true">
        <span style={{ width: `${Math.min(100, value.used)}%` }} />
      </span>
      <span className="usage-value">{formatNumber(value.used / 100, { style: "percent", maximumFractionDigits: 0 })}</span>
      {reset && (
        <span className="usage-reset" title={t("usage.resetIn", { time: reset })}>
          <RotateCcw size={11} aria-label={t("usage.resetIn", { time: reset })} />
          {reset}
        </span>
      )}
    </span>
  );
}

/** RF-60: one row per provider with its 5-hour and weekly windows. */
export default function UsageBar({ usage, onRefresh }) {
  const { t } = useTranslation();
  const [renewing, setRenewing] = useState(false);
  const [renewError, setRenewError] = useState(null);

  const handleManualRenew = async () => {
    setRenewing(true);
    setRenewError(null);
    try {
      const res = await fetch("/api/usage/claude/renew", { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setRenewError(apiErrorDescriptor(data, res.status));
      } else {
        if (typeof onRefresh === "function") {
          onRefresh();
        }
      }
    } catch {
      setRenewError({ key: "errors.connection" });
    } finally {
      setRenewing(false);
    }
  };

  const rows = usageRows(usage);
  if (rows.length === 0) return null;
  return (
    <section className="usage-bar" aria-label={t("usage.title")} data-testid="usage-bar">
      {rows.map((row) => (
        <div key={row.key} className={`usage-row prov-${row.provider}`} data-testid={`usage-${row.key}`}>
          <span className="usage-provider">
            <Gauge size={14} aria-hidden="true" />
            {providerOf({ provider: row.provider }).label}
            {row.group && <small>{row.group}</small>}
          </span>
          <Window name={t("usage.window5h")} value={row.windows["5h"]} t={t} />
          <Window name={t("usage.window7d")} value={row.windows["7d"]} t={t} />
          {row.staleSince && (
            <span className="usage-stale" data-testid={`usage-stale-${row.key}`}>
              {t(row.provider === "claude-code" ? "usage.staleClaude" : "usage.stale", { time: timeAgo(row.staleSince) })}
              {row.provider === "claude-code" && (
                <button
                  type="button"
                  className="btn-renew-claude"
                  data-testid="btn-claude-renew"
                  disabled={renewing}
                  onClick={handleManualRenew}
                >
                  {renewing ? t("usage.renewing") : t("usage.renewClaude")}
                </button>
              )}
            </span>
          )}
          {row.provider === "claude-code" && renewError && (
            <div className="usage-renew-error" role="alert" data-testid="claude-renew-error">
              {t(renewError.key, renewError.params)}
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
