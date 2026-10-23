import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { SlidersHorizontal } from "lucide-react";
import { apiErrorDescriptor } from "./i18n/errors";

/**
 * RF-62: "Compact" and "Effort" for the session, in the header. Shown only
 * for a supported session (a Claude Code CLI in tmux), enabled when idle. The backend decides and types a fixed command.
 */
export default function SessionControls({ session }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState("medium");
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState(false);
  // The command was typed but a dialog waits for the user on the PC.
  const [needsConfirmation, setNeedsConfirmation] = useState(false);
  const [error, setError] = useState(null);
  const controls = session?.controls;
  if (!controls || Object.keys(controls).length === 0) return null;
  // Only where the controls can work (a Claude Code CLI in tmux): elsewhere nothing to show.
  if (controls.compact !== "supported") return null;

  const levels = controls.effort_levels || [];
  const state = controls.compact;
  const idle = session.state === "idle";
  const enabled = state === "supported" && idle && !pending;
  let reason = null;
  if (!idle) reason = t("controls.busy");

  const run = async (path, body = {}, confirmed = false) => {
    setPending(true); setDone(false); setNeedsConfirmation(false); setError(null);
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(session.id)}/${path}`, {
        method: "POST",
        headers: confirmed || body.level ? { "Content-Type": "application/json" } : undefined,
        body: confirmed ? JSON.stringify({ ...body, confirm_uncertain: true })
          : Object.keys(body).length ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        if (data?.code === "session.control_uncertain" && !confirmed) {
          // The previous command may already have been applied (the server remembers it for every
          // tab): repeat only if the user says so.
          setPending(false);
          if (window.confirm(t("controls.confirmRepeat"))) await run(path, body, true);
          return;
        }
        setError(apiErrorDescriptor(data, res.status));
      } else {
        const data = await res.json().catch(() => null);
        if (data?.state === "needs_confirmation") setNeedsConfirmation(true);
        else setDone(true);
      }
    } catch {
      // The request may or may not have reached the backend.
      setError({ key: "errors.commandUncertain" });
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
          <p className="composer-note" data-testid="control-effort-note">
            {t("controls.effortPersists")}{" "}
            <a href="https://code.claude.com/docs/en/model-config#adjust-effort-level" target="_blank"
              rel="noopener noreferrer">{t("controls.effortDocs")}</a>
          </p>
          {reason && <p className="composer-note" data-testid="control-explanation">{reason}</p>}
          {needsConfirmation && (
            <p className="composer-note" role="status" data-testid="control-needs-confirmation">{t("controls.needsConfirmation")}</p>
          )}
          {done && <p className="composer-note" role="status" data-testid="control-done">{t("controls.done")}</p>}
          {error && <p className="composer-note" role="alert" data-testid="control-error">{t(error.key, error.params)}</p>}
        </div>
      )}
    </div>
  );
}
