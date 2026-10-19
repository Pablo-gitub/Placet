import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Gauge, RotateCcw, SlidersHorizontal } from "lucide-react";
import { formatNumber } from "./i18n/formatters";
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
export default function UsageBar({ usage }) {
  const { t } = useTranslation();
  const [showClaudeMenu, setShowClaudeMenu] = useState(false);
  const [autoRenew, setAutoRenew] = useState(false);

  useEffect(() => {
    fetch("/api/settings/claude-renew")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && typeof data.enabled === "boolean") {
          setAutoRenew(data.enabled);
        }
      })
      .catch(() => {});
  }, []);

  const handleToggleAutoRenew = () => {
    const next = !autoRenew;
    setAutoRenew(next);
    fetch("/api/settings/claude-renew", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next }),
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && typeof data.enabled === "boolean") {
          setAutoRenew(data.enabled);
        }
      })
      .catch(() => {
        setAutoRenew(!next);
      });
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
            {row.provider === "claude-code" && (
              <span className="claude-menu-wrap">
                <button
                  type="button"
                  className="btn-usage-menu"
                  data-testid="btn-claude-menu"
                  title={t("usage.claudeOptions")}
                  aria-label={t("usage.claudeOptions")}
                  onClick={() => setShowClaudeMenu((prev) => !prev)}
                >
                  <SlidersHorizontal size={12} aria-hidden="true" />
                </button>
                {showClaudeMenu && (
                  <div className="claude-menu-popover" data-testid="claude-menu-popover">
                    <label className="claude-menu-item">
                      <input
                        type="checkbox"
                        data-testid="toggle-claude-auto-renew"
                        checked={autoRenew}
                        onChange={handleToggleAutoRenew}
                      />
                      <span>{t("usage.autoRenew")}</span>
                    </label>
                  </div>
                )}
              </span>
            )}
          </span>
          <Window name={t("usage.window5h")} value={row.windows["5h"]} t={t} />
          <Window name={t("usage.window7d")} value={row.windows["7d"]} t={t} />
          {row.staleSince && (
            <span className="usage-stale" data-testid={`usage-stale-${row.key}`}>
              {t(row.provider === "claude-code" ? "usage.staleClaude" : "usage.stale", { time: timeAgo(row.staleSince) })}
            </span>
          )}
        </div>
      ))}
    </section>
  );
}
