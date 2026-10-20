import React, { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, BarChart2, ShieldAlert } from "lucide-react";
import { providerOf } from "./sessionInfo";
import { apiErrorMessage } from "./i18n/errors";

function formatProviderLabel(prov, t) {
  if (!prov) return "";
  if (prov === "human") return t("stats.human");
  return providerOf({ provider: prov, agent_id: prov }).label;
}

function formatYMD(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function formatReplyTime(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "-";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${(seconds / 60).toFixed(1)}m`;
}

function computePeriodRange(periodKey) {
  const now = new Date();
  const to = formatYMD(now);
  if (periodKey === "today") {
    return { from: to, to };
  }
  if (periodKey === "30d") {
    const fromDate = new Date(now.getTime() - 29 * 86400000);
    return { from: formatYMD(fromDate), to };
  }
  // Default: 7d
  const fromDate = new Date(now.getTime() - 6 * 86400000);
  return { from: formatYMD(fromDate), to };
}

/**
 * Statistics dashboard (RF-64, RF-64b).
 * Displays request volume, automation rates, reply times, distributions,
 * and provider usage history charts.
 */
export default function Statistics({ onBack }) {
  const { t } = useTranslation();
  const [period, setPeriod] = useState("7d");
  const [dateRange, setDateRange] = useState(() => computePeriodRange("7d"));
  const [customFrom, setCustomFrom] = useState(() => computePeriodRange("7d").from);
  const [customTo, setCustomTo] = useState(() => computePeriodRange("7d").to);

  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    const params = new URLSearchParams({ from: dateRange.from, to: dateRange.to, tz });
    fetch(`/api/stats?${params.toString()}`)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          const msg = body?.code ? apiErrorMessage(body, res.status, t) : t("stats.loadError");
          throw new Error(msg);
        }
        return res.json();
      })
      .then((data) => {
        if (active) {
          setStats(data);
          setError(null);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err?.message || t("stats.loadError"));
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [dateRange, t]);

  function handleSelectPeriod(key) {
    setPeriod(key);
    if (key !== "custom") {
      setLoading(true);
      const nextRange = computePeriodRange(key);
      setDateRange(nextRange);
    }
  }

  function handleApplyCustom() {
    if (!customFrom || !customTo) return;
    setLoading(true);
    setDateRange({ from: customFrom, to: customTo });
  }

  const automationRate = useMemo(() => {
    if (!stats || !stats.total_requests) return "0%";
    const auto = stats.approval?.automatic || 0;
    return `${Math.round((auto / stats.total_requests) * 100)}%`;
  }, [stats]);

  const thresholdDays = useMemo(() => {
    if (!stats || !stats.usage) return 0;
    return stats.usage.reduce((sum, u) => sum + (u.threshold_days || 0), 0);
  }, [stats]);

  const maxVolume = useMemo(() => {
    if (!stats || !stats.per_day || stats.per_day.length === 0) return 1;
    return Math.max(1, ...stats.per_day.map((d) => d.count || 0));
  }, [stats]);

  return (
    <section className="stats-view" data-testid="stats-section">
      <div className="stats-header">
        <button
          type="button"
          className="link-button project-detail-back-btn"
          data-testid="stats-back"
          onClick={onBack}
        >
          <ArrowLeft size={16} /> {t("settings.title")}
        </button>
        <h2>
          <BarChart2 size={20} className="stats-header-icon" />
          {t("stats.title")}
        </h2>
        <p className="commands-hint">{t("settings.statsHint")}</p>
      </div>

      <div className="stats-period-selector" data-testid="stats-period-selector">
        <div className="stats-chips">
          <button
            type="button"
            className={`period-chip ${period === "today" ? "active" : ""}`}
            data-testid="period-chip-today"
            onClick={() => handleSelectPeriod("today")}
          >
            {t("stats.today")}
          </button>
          <button
            type="button"
            className={`period-chip ${period === "7d" ? "active" : ""}`}
            data-testid="period-chip-7d"
            onClick={() => handleSelectPeriod("7d")}
          >
            {t("stats.days7")}
          </button>
          <button
            type="button"
            className={`period-chip ${period === "30d" ? "active" : ""}`}
            data-testid="period-chip-30d"
            onClick={() => handleSelectPeriod("30d")}
          >
            {t("stats.days30")}
          </button>
          <button
            type="button"
            className={`period-chip ${period === "custom" ? "active" : ""}`}
            data-testid="period-chip-custom"
            onClick={() => handleSelectPeriod("custom")}
          >
            {t("stats.custom")}
          </button>
        </div>

        {period === "custom" && (
          <div className="stats-custom-range" data-testid="stats-custom-controls">
            <label className="stats-date-label">
              <span>{t("stats.from")}</span>
              <input
                type="date"
                className="stats-date-input"
                data-testid="stats-custom-from"
                value={customFrom}
                onChange={(e) => setCustomFrom(e.target.value)}
              />
            </label>
            <label className="stats-date-label">
              <span>{t("stats.to")}</span>
              <input
                type="date"
                className="stats-date-input"
                data-testid="stats-custom-to"
                value={customTo}
                onChange={(e) => setCustomTo(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn btn-secondary stats-custom-apply-btn"
              data-testid="stats-custom-apply"
              onClick={handleApplyCustom}
            >
              {t("stats.apply")}
            </button>
          </div>
        )}
      </div>

      {loading && !stats && <p role="status" className="stats-notice">{t("stats.loading")}</p>}
      {error && (
        <p role="status" className="stats-notice error">
          <ShieldAlert size={16} /> {typeof error === "string" ? error : t("stats.loadError")}
        </p>
      )}

      {stats && (
        <div className="stats-content">
          {/* Summary KPI Cards */}
          <div className="stats-cards-grid">
            <div className="card stats-kpi-card">
              <span className="stats-kpi-label">{t("stats.totalRequests")}</span>
              <strong className="stats-kpi-value" data-testid="stats-total-requests">
                {stats.total_requests}
              </strong>
            </div>
            <div className="card stats-kpi-card">
              <span className="stats-kpi-label">{t("stats.automationRate")}</span>
              <strong className="stats-kpi-value" data-testid="stats-automation-rate">
                {automationRate}
              </strong>
            </div>
            <div className="card stats-kpi-card">
              <span className="stats-kpi-label">{t("stats.meanReply")}</span>
              <strong className="stats-kpi-value" data-testid="stats-mean-reply">
                {formatReplyTime(stats.mean_reply_seconds)}
              </strong>
            </div>
            <div className="card stats-kpi-card">
              <span className="stats-kpi-label">{t("stats.thresholdDays")}</span>
              <strong className="stats-kpi-value" data-testid="stats-threshold-days">
                {thresholdDays}
              </strong>
            </div>
          </div>

          {/* Volume Over Time Chart */}
          <div className="card stats-section-card">
            <h3 className="section-label">{t("stats.volumeOverTime")}</h3>
            <div className="stats-chart-wrapper" data-testid="stats-volume-chart">
              {stats.per_day && stats.per_day.length > 0 ? (
                <svg
                  className="stats-svg-chart"
                  viewBox={`0 0 ${Math.max(300, stats.per_day.length * 44)} 130`}
                  role="img"
                  aria-label={t("stats.volumeOverTime")}
                >
                  <line x1="0" y1="100" x2="100%" y2="100" className="stats-chart-axis" />
                  {stats.per_day.map((d, idx) => {
                    const barHeight = Math.max(3, (d.count / maxVolume) * 80);
                    const x = idx * 44 + 14;
                    const y = 100 - barHeight;
                    const shortDate = d.date.slice(5); // MM-DD
                    return (
                      <g key={d.date} className="stats-chart-bar-group">
                        <title>{`${d.date}: ${d.count} ${t("stats.requests")} (${formatReplyTime(d.mean_reply_seconds)})`}</title>
                        <rect
                          x={x}
                          y={y}
                          width={24}
                          height={barHeight}
                          rx={3}
                          className="stats-chart-bar"
                        />
                        <text
                          x={x + 12}
                          y={y - 4}
                          textAnchor="middle"
                          className="stats-chart-val-text"
                        >
                          {d.count > 0 ? d.count : ""}
                        </text>
                        <text
                          x={x + 12}
                          y={118}
                          textAnchor="middle"
                          className="stats-chart-axis-text"
                        >
                          {shortDate}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              ) : (
                <p className="stats-no-data">{t("stats.noData")}</p>
              )}
            </div>
          </div>

          {/* Distributions: Relations and Projects */}
          <div className="stats-split-grid">
            <div className="card stats-section-card" data-testid="stats-relations">
              <h3 className="section-label">{t("stats.byRelationType")}</h3>
              {stats.by_relation_type && Object.keys(stats.by_relation_type).length > 0 ? (
                <div className="stats-dist-list">
                  {Object.entries(stats.by_relation_type).map(([name, count]) => {
                    const pct = Math.round((count / (stats.total_requests || 1)) * 100);
                    return (
                      <div key={name} className="stats-dist-row">
                        <div className="stats-dist-header">
                          <span className="stats-dist-name">{name}</span>
                          <span className="stats-dist-count">{count}</span>
                        </div>
                        <div className="stats-dist-bar-track">
                          <span className="stats-dist-bar-inline" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="stats-no-data">{t("stats.noData")}</p>
              )}
            </div>

            <div className="card stats-section-card" data-testid="stats-projects">
              <h3 className="section-label">{t("stats.byProject")}</h3>
              {stats.by_project && stats.by_project.length > 0 ? (
                <div className="stats-dist-list">
                  {stats.by_project.map((proj) => {
                    const pct = Math.round((proj.count / (stats.total_requests || 1)) * 100);
                    return (
                      <div key={proj.id ?? "none"} className="stats-dist-row">
                        <div className="stats-dist-header">
                          <span className="stats-dist-name">{proj.name}</span>
                          <span className="stats-dist-count">{proj.count}</span>
                        </div>
                        <div className="stats-dist-bar-track">
                          <span className="stats-dist-bar-inline" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="stats-no-data">{t("stats.noData")}</p>
              )}
            </div>
          </div>

          {/* Provider Distributions */}
          <div className="stats-split-grid">
            <div className="card stats-section-card">
              <h3 className="section-label">{`${t("stats.byProvider")} (${t("stats.sender")})`}</h3>
              {stats.by_sender_provider && Object.keys(stats.by_sender_provider).length > 0 ? (
                <div className="stats-dist-list">
                  {Object.entries(stats.by_sender_provider).map(([prov, count]) => {
                    const pct = Math.round((count / (stats.total_requests || 1)) * 100);
                    return (
                      <div key={prov} className="stats-dist-row">
                        <div className="stats-dist-header">
                          <span className="stats-dist-name">{formatProviderLabel(prov, t)}</span>
                          <span className="stats-dist-count">{count}</span>
                        </div>
                        <div className="stats-dist-bar-track">
                          <span className="stats-dist-bar-inline" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="stats-no-data">{t("stats.noData")}</p>
              )}
            </div>

            <div className="card stats-section-card">
              <h3 className="section-label">{`${t("stats.byProvider")} (${t("stats.recipient")})`}</h3>
              {stats.by_recipient_provider && Object.keys(stats.by_recipient_provider).length > 0 ? (
                <div className="stats-dist-list">
                  {Object.entries(stats.by_recipient_provider).map(([prov, count]) => {
                    const pct = Math.round((count / (stats.total_requests || 1)) * 100);
                    return (
                      <div key={prov} className="stats-dist-row">
                        <div className="stats-dist-header">
                          <span className="stats-dist-name">{formatProviderLabel(prov, t)}</span>
                          <span className="stats-dist-count">{count}</span>
                        </div>
                        <div className="stats-dist-bar-track">
                          <span className="stats-dist-bar-inline" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="stats-no-data">{t("stats.noData")}</p>
              )}
            </div>
          </div>

          {/* Decisions and User Messages */}
          <div className="stats-split-grid">
            <div className="card stats-section-card" data-testid="stats-outcomes">
              <h3 className="section-label">{t("stats.outcomes")}</h3>
              <div className="stats-meta-grid">
                <div className="stats-meta-item">
                  <span className="stats-meta-label">{t("stats.approved")}</span>
                  <strong>{stats.decisions?.approved ?? 0}</strong>
                </div>
                <div className="stats-meta-item">
                  <span className="stats-meta-label">{t("stats.edited")}</span>
                  <strong>{stats.decisions?.edited ?? 0}</strong>
                </div>
                <div className="stats-meta-item">
                  <span className="stats-meta-label">{t("stats.rejected")}</span>
                  <strong>{stats.decisions?.rejected ?? 0}</strong>
                </div>
                <div className="stats-meta-item">
                  <span className="stats-meta-label">{t("stats.expired")}</span>
                  <strong>{stats.decisions?.expired ?? 0}</strong>
                </div>
                <div className="stats-meta-item">
                  <span className="stats-meta-label">{t("stats.cancelled")}</span>
                  <strong>{stats.decisions?.cancelled ?? 0}</strong>
                </div>
              </div>
            </div>

            <div className="card stats-section-card">
              <h3 className="section-label">{t("stats.userMessages")}</h3>
              <div className="stats-meta-grid">
                <div className="stats-meta-item">
                  <span className="stats-meta-label">{t("stats.userSent")}</span>
                  <strong>{stats.user_messages?.requests ?? 0}</strong>
                </div>
                <div className="stats-meta-item">
                  <span className="stats-meta-label">{t("stats.userReplies")}</span>
                  <strong>{stats.user_messages?.replies ?? 0}</strong>
                </div>
              </div>
            </div>
          </div>

          {/* Provider Usage History (RF-64b) */}
          <div className="card stats-section-card">
            <h3 className="section-label">{t("stats.providerUsage")}</h3>
            {stats.usage && stats.usage.length > 0 ? (
              <div className="stats-usage-charts">
                {stats.usage.map((u) => {
                  const key = `${u.provider}-${u.bucket_id || u.window}`;
                  const thresholdY = 120 - ((u.threshold || 80) / 100) * 75;
                  const daysCount = u.peaks?.length || 1;
                  const provLabel = formatProviderLabel(u.provider, t);
                  return (
                    <div
                      key={key}
                      className="stats-usage-block"
                      data-testid={`stats-usage-chart-${key}`}
                    >
                      <div className="stats-usage-header">
                        <strong>{provLabel}</strong>
                        <span className="stats-usage-badge">{u.window}</span>
                        {u.threshold_days > 0 && (
                          <span className="stats-usage-alert">
                            {`${u.threshold_days} ${t("stats.thresholdDays").toLowerCase()}`}
                          </span>
                        )}
                      </div>
                      <div className="stats-chart-wrapper">
                        <svg
                          className="stats-svg-chart"
                          viewBox={`0 0 ${Math.max(280, daysCount * 44 + 14)} 150`}
                          role="img"
                          aria-label={`${provLabel} ${u.window}`}
                        >
                          {/* Baseline and threshold line */}
                          <line x1="0" y1="120" x2="100%" y2="120" className="stats-chart-axis" />
                          <line
                            x1="0"
                            y1={thresholdY}
                            x2="100%"
                            y2={thresholdY}
                            className="stats-usage-threshold-line"
                          />
                          <text
                            x="4"
                            y={thresholdY - 4}
                            className="stats-usage-threshold-text"
                          >
                            {t("stats.thresholdLine", { threshold: u.threshold })}
                          </text>

                          {/* Bars for daily peaks */}
                          {u.peaks?.map((p, pIdx) => {
                            const hasPeak = p.peak_used_percent !== null && p.peak_used_percent !== undefined;
                            const barHeight = hasPeak
                              ? Math.min(75, Math.max(2, (p.peak_used_percent / 100) * 75))
                              : 0;
                            const x = pIdx * 44 + 14;
                            const y = 120 - barHeight;
                            const isAbove = hasPeak && p.peak_used_percent >= (u.threshold || 80);
                            const shortDate = p.date.slice(5);
                            return (
                              <g key={p.date} data-date={p.date} className="stats-chart-bar-group">
                                <title>
                                  {hasPeak ? `${p.date}: ${Math.round(p.peak_used_percent)}%` : `${p.date}: -`}
                                </title>
                                {hasPeak && (
                                  <>
                                    <rect
                                      x={x}
                                      y={y}
                                      width={24}
                                      height={barHeight}
                                      rx={3}
                                      className={`stats-chart-bar ${isAbove ? "threshold-exceeded" : ""}`}
                                    />
                                    <text
                                      x={x + 12}
                                      y={y - 5}
                                      textAnchor="middle"
                                      className="stats-chart-val-text"
                                    >
                                      {`${Math.round(p.peak_used_percent)}%`}
                                    </text>
                                  </>
                                )}
                                <text
                                  x={x + 12}
                                  y={138}
                                  textAnchor="middle"
                                  className="stats-chart-axis-text"
                                >
                                  {shortDate}
                                </text>
                              </g>
                            );
                          })}
                        </svg>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="stats-no-data" data-testid="stats-no-usage">
                {t("stats.noUsageData")}
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
