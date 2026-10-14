import React from "react";
import { Monitor, SquareTerminal, Circle } from "lucide-react";
import { sessionName, sessionStatus } from "./sessionInfo";

const CLIENT_LABELS = {
  "claude-desktop": "App desktop",
  cli: "Terminale",
};

function since(iso) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "ora";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min fa`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h fa`;
  return new Date(iso).toLocaleDateString();
}

/** Open sessions of every agent, grouped by agent (from their hooks). */
export default function Sessions({ sessions, agents }) {
  const byAgent = {};
  for (const s of sessions) (byAgent[s.agent_id] ||= []).push(s);
  const agentIds = [...new Set([...Object.keys(agents || {}), ...Object.keys(byAgent)])];

  return (
    <div data-testid="sessions-section">
      {agentIds.map((agentId) => (
        <section key={agentId} className="session-group">
          <h2 className="session-group-title">{agents?.[agentId]?.name || agentId}</h2>
          {(byAgent[agentId] || []).length === 0 && (
            <p className="session-empty">Nessuna sessione aperta.</p>
          )}
          {(byAgent[agentId] || []).map((s) => {
            const status = sessionStatus(s);
            return (
              <div key={s.id} className="card session-card" data-testid={`session-${s.id}`}>
                <div className="session-row">
                  {s.tmux_session ? <SquareTerminal size={16} /> : <Monitor size={16} />}
                  <strong className="session-name">{sessionName(s)}</strong>
                  <span className={`session-state state-${status.key}`} data-testid={`session-state-${s.id}`}>
                    <Circle size={8} fill="currentColor" />
                    {status.label}
                  </span>
                </div>
                <div className="session-meta">
                  {s.cwd && <span title={s.cwd}>{s.cwd}</span>}
                  <span>
                    {CLIENT_LABELS[s.entrypoint] || s.entrypoint || "Client sconosciuto"}
                    {s.tmux_session ? ` · tmux ${s.tmux_session}` : ""} · {since(s.last_seen_at)}
                  </span>
                </div>
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}
