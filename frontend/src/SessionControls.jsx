import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { SlidersHorizontal } from "lucide-react";
import { apiErrorDescriptor } from "./i18n/errors";

// RF-62: what Claude Code's `--effort` accepts (the backend holds the same list).
const EFFORT_LEVELS = { "claude-code": ["low", "medium", "high", "xhigh", "max"] };

/**
 * RF-62: "Compact" and "Effort" for the session, in the header. Enabled only
 * for a supported idle session (a Claude Code CLI in tmux); otherwise the
 * reason is shown. The backend decides and types a fixed command.
 */
export default function SessionControls({ session }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState("medium");
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState(null);
  const controls = session?.controls;
  if (!controls || Object.keys(controls).length === 0) return null;

  const levels = EFFORT_LEVELS[session.provider] || [];
  const state = controls.compact;
  const idle = session.state === "idle";
  const enabled = state === "supported" && idle && !pending;
  let reason = null;
  if (state === "pc_only") reason = t("controls.pcOnly");
  else if (state !== "supported") reason = t("controls.unsupported");
  else if (!idle) reason = t("controls.busy");

  const run = async (path, body) => {
    setPending(true); setDone(false); setError(null);
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(session.id)}/${path}`, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        setError(apiErrorDescriptor(data, res.status));
      } else {
        setDone(true);
      }
    } catch {
      setError({ key: "errors.operation", params: { status: 0 } });
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="session-controls">
      <button type="button" className="icon-button" data-testid="session-controls-toggle"
        aria-label={t("controls.title")} aria-expanded={open} onClick={() => setOpen(!open)}>
        <SlidersHorizontal size={18} />
      </button>
      {open && (
        <div className="session-controls-panel" role="group" aria-label={t("controls.title")}>
          <button type="button" data-testid="control-compact" disabled={!enabled} onClick={() => run("compact")}>
            {t("controls.compact")}
          </button>
          <div className="session-controls-effort">
            <label htmlFor="control-effort-level">{t("controls.effort")}</label>
            <select id="control-effort-level" data-testid="control-effort-level" value={level}
              disabled={!enabled} onChange={(e) => setLevel(e.target.value)}>
              {levels.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
            <button type="button" data-testid="control-effort" disabled={!enabled || levels.length === 0}
              onClick={() => run("effort", { level })}>
              {t("controls.apply")}
            </button>
          </div>
          {reason && <p className="composer-note" data-testid="control-explanation">{reason}</p>}
          {done && <p className="composer-note" role="status" data-testid="control-done">{t("controls.done")}</p>}
          {error && <p className="composer-note" role="alert" data-testid="control-error">{t(error.key, error.params)}</p>}
        </div>
      )}
    </div>
  );
}
