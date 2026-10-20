import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export default function NotificationPreferences() {
  const { t } = useTranslation();
  const [prefs, setPrefs] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/settings/notifications")
      .then(async (res) => {
        if (!res.ok) throw new Error("Load failed");
        const data = await res.json();
        if (!cancelled) setPrefs(data);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!prefs) {
    return error ? (
      <p className="composer-note" role="alert" data-testid="notify-prefs-error">
        {t("settings.loadError")}
      </p>
    ) : null;
  }

  const update = async (patch) => {
    const previous = prefs;
    const next = { ...prefs, ...patch };
    setPrefs(next);
    setError(false);
    try {
      const res = await fetch("/api/settings/notifications", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
      if (!res.ok) throw new Error("Save failed");
      const saved = await res.json();
      setPrefs(saved);
    } catch {
      setPrefs(previous);
      setError(true);
    }
  };

  return (
    <div className="notification-preferences" data-testid="notification-preferences">
      <label className="settings-row settings-check">
        <span className="settings-row-text">
          <strong>{t("settings.notifyNeedsYouTitle")}</strong>
          <small>{t("settings.notifyNeedsYouHint")}</small>
        </span>
        <input
          type="checkbox"
          data-testid="notify-pref-needs-you"
          checked={prefs.needs_you}
          onChange={(e) => update({ needs_you: e.target.checked })}
        />
      </label>

      <label className="settings-row settings-check">
        <span className="settings-row-text">
          <strong>{t("settings.notifyProblemsTitle")}</strong>
          <small>{t("settings.notifyProblemsHint")}</small>
        </span>
        <input
          type="checkbox"
          data-testid="notify-pref-problems"
          checked={prefs.problems}
          onChange={(e) => update({ problems: e.target.checked })}
        />
      </label>

      <div className="settings-turn-end">
        <div className="settings-row-text">
          <strong>{t("settings.turnEndTitle")}</strong>
        </div>
        <div className="settings-options" role="radiogroup" aria-label={t("settings.turnEndTitle")}>
          <label className="option-item">
            <input
              type="radio"
              name="turn_end"
              value="all"
              data-testid="notify-turn-end-all"
              checked={prefs.turn_end === "all"}
              onChange={() => update({ turn_end: "all" })}
            />
            <span>{t("settings.turnEndAll")}</span>
          </label>
          <label className="option-item">
            <input
              type="radio"
              name="turn_end"
              value="placet"
              data-testid="notify-turn-end-placet"
              checked={prefs.turn_end === "placet"}
              onChange={() => update({ turn_end: "placet" })}
            />
            <span>{t("settings.turnEndPlacet")}</span>
          </label>
          <small className="settings-option-hint">{t("settings.turnEndPlacetHint")}</small>
          <label className="option-item">
            <input
              type="radio"
              name="turn_end"
              value="off"
              data-testid="notify-turn-end-off"
              checked={prefs.turn_end === "off"}
              onChange={() => update({ turn_end: "off" })}
            />
            <span>{t("settings.turnEndOff")}</span>
          </label>
        </div>
      </div>

      {error && (
        <p className="composer-note" role="alert" data-testid="notify-prefs-error">
          {t("settings.saveError")}
        </p>
      )}
    </div>
  );
}
