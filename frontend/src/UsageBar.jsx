import React from "react";
import { useTranslation } from "react-i18next";
import { Gauge, RotateCcw } from "lucide-react";
import { formatNumber } from "./i18n/formatters";
import { providerOf } from "./sessionInfo";
import { untilReset, usageRows } from "./usage";

const level = (used) => (used >= 90 ? "high" : used >= 70 ? "mid" : "low");

function Window({ name, value, t }) {
  if (!value) return null;
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
        </div>
      ))}
    </section>
  );
}
