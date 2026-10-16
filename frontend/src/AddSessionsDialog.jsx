import React, { useState } from "react";
import { X } from "lucide-react";
import {
  PROVIDERS,
  activityOf,
  byActivity,
  providerOf,
  sessionName,
  sessionPlace,
  sessionStatus,
  timeAgo,
} from "./sessionInfo";

/**
 * Choose a provider, see its open sessions, tick the ones to follow in the
 * home (RF-41). Sessions already added are ticked.
 */
export default function AddSessionsDialog({ sessions, onToggle, onOpen, onClose }) {
  const open = sessions.filter((s) => !s.ended_at);
  const providers = [
    ...PROVIDERS,
    ...[...new Map(open.map((s) => [providerOf(s).key, providerOf(s)])).values()].filter(
      (p) => !PROVIDERS.some((known) => known.key === p.key)
    ),
  ];
  const firstWithSessions = providers.find((p) => open.some((s) => providerOf(s).key === p.key));
  const [provider, setProvider] = useState((firstWithSessions || providers[0]).key);
  const listed = open.filter((s) => providerOf(s).key === provider).sort(byActivity);

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Aggiungi sessioni"
        data-testid="add-sessions-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-header">
          <h2>Sessioni aperte</h2>
          <button type="button" className="icon-button" aria-label="Chiudi" onClick={onClose}>
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
              className={`provider-tab${p.key === provider ? " active" : ""}`}
              onClick={() => setProvider(p.key)}
            >
              {p.label}
              <span className="provider-count">{open.filter((s) => providerOf(s).key === p.key).length}</span>
            </button>
          ))}
        </div>
        <div className="dialog-body">
          {listed.length === 0 && (
            <p className="empty-hint" data-testid="no-open-sessions">
              Nessuna sessione aperta. Le sessioni compaiono qui quando gli hook di Placet sono attivi.
            </p>
          )}
          {listed.map((s) => {
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
                  <strong>{sessionName(s)}</strong>
                  <span className="session-meta-line">
                    {[sessionPlace(s), status.label, timeAgo(activityOf(s))].filter(Boolean).join(" · ")}
                  </span>
                </label>
                <button type="button" className="link-button" onClick={() => onOpen(s.id)}>
                  Apri
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
