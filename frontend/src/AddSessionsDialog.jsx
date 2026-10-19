import { useTranslation } from "react-i18next";
import React, { useState } from "react";
import { ChevronRight, Folder, X } from "lucide-react";
import {
  PROVIDERS,
  activityOf,
  byActivity,
  providerOf,
  sessionLabels,
  sessionName,
  sessionPlace,
  sessionStatus,
  timeAgo,
} from "./sessionInfo";

/**
 * The provider's sessions by working folder, most recently active first.
 * Folders share a name ("app" in two places): add parent folders until the
 * labels differ.
 */
function folderGroups(sessions) {
  const groups = new Map();
  for (const s of [...sessions].sort(byActivity)) {
    const key = s.cwd || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  }
  const parts = (key) => key.split("/").filter(Boolean);
  const label = (key, depth) => parts(key).slice(-depth).join("/");
  const keys = [...groups.keys()].filter(Boolean);
  const labels = {};
  for (const key of keys) {
    let depth = 1;
    while (depth < parts(key).length
      && keys.some((other) => other !== key && label(other, depth) === label(key, depth))) depth += 1;
    labels[key] = label(key, depth);
  }
  return [...groups.entries()].map(([key, list]) => ({ key, label: labels[key] || null, sessions: list }));
}

/**
 * Choose a provider, see all its sessions, tick the ones to follow in the
 * home (RF-41). Sessions already added are ticked.
 */
export default function AddSessionsDialog({ sessions, onToggle, onOpen, onClose }) {
  const { t } = useTranslation();
  const providers = [
    ...PROVIDERS,
    ...[...new Map(sessions.map((s) => [providerOf(s).key, providerOf(s)])).values()].filter(
      (p) => !PROVIDERS.some((known) => known.key === p.key)
    ),
  ];
  const firstWithSessions = providers.find((p) => sessions.some((s) => providerOf(s).key === p.key));
  const [provider, setProvider] = useState((firstWithSessions || providers[0]).key);
  const listed = sessions.filter((s) => providerOf(s).key === provider).sort(byActivity);

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={t("picker.label")}
        data-testid="add-sessions-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-header">
          <h2>{t("picker.title")}</h2>
          <button type="button" className="icon-button" aria-label={t("picker.close")} onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="provider-tabs" role="tablist">
          {providers.map((p) => (
            <button
              key={p.key}
              type="button"
              role="tab"
              aria-selected={p.key === provider}
              data-testid={`provider-tab-${p.key}`}
              className={`provider-tab prov-${p.key}${p.key === provider ? " active" : ""}`}
              onClick={() => setProvider(p.key)}
            >
              {p.label}
              <span className="provider-count">{sessions.filter((s) => providerOf(s).key === p.key).length}</span>
            </button>
          ))}
        </div>
        <div className="dialog-body dialog-scroll" data-testid="add-sessions-body">
          <p className="empty-hint">{t("picker.hint")}</p>
          {listed.length === 0 && (
            <p className="empty-hint" data-testid="no-open-sessions">
              {t("picker.empty")}
            </p>
          )}
          {(() => {
            const labels = sessionLabels(listed);
            return folderGroups(listed).map((group, index) => (
              <details key={group.key} className="pick-folder" data-testid={`pick-folder-${group.key}`}
                open={index === 0 || group.sessions.some((s) => s.pinned)}>
                <summary title={group.key || undefined}>
                  <ChevronRight size={14} className="pick-folder-chevron" aria-hidden="true" />
                  <Folder size={14} aria-hidden="true" />
                  <span className="pick-folder-name">{group.label || t("picker.noFolder")}</span>
                  <span className="provider-count" data-testid="pick-folder-count">{group.sessions.length}</span>
                </summary>
                {group.sessions.map((s) => {
                  const status = sessionStatus(s);
                  return (
                    <div key={s.id} className="pick-row" data-testid={`pick-${s.id}`}>
                      <input
                        type="checkbox"
                        id={`pick-${s.id}`}
                        data-testid={`pick-toggle-${s.id}`}
                        checked={!!s.pinned}
                        onChange={(e) => onToggle(s, e.target.checked)}
                      />
                      <label htmlFor={`pick-${s.id}`} className="pick-label">
                        <strong>{labels[s.id] || sessionName(s)}</strong>
                        <span className="session-meta-line">
                          {[sessionPlace(s), status.label, timeAgo(activityOf(s))].filter(Boolean).join(" · ")}
                        </span>
                      </label>
                      <button type="button" className="link-button" onClick={() => onOpen(s.id)}>
                        {t("picker.open")}
                      </button>
                    </div>
                  );
                })}
              </details>
            ));
          })()}
        </div>
      </div>
    </div>
  );
}
