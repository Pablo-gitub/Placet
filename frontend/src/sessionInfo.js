/** Display helpers for agent sessions. */

const CLIENT_LABELS = {
  "claude-desktop": "app desktop",
  "codex-desktop": "app desktop",
  cli: "terminale",
};

/** Supported providers, in the order the PWA shows them. */
export const PROVIDERS = [
  { key: "claude-code", label: "Claude", agent: "claude" },
  { key: "codex", label: "Codex", agent: "codex" },
  { key: "antigravity", label: "Antigravity", agent: "gemini" },
];

/** The provider of a session; old sessions may lack it, so guess from the agent. */
export function providerOf(session) {
  return (
    PROVIDERS.find((p) => p.key === session.provider) ||
    PROVIDERS.find((p) => p.agent === session.agent_id) || {
      key: session.provider || session.agent_id,
      label: session.provider || session.agent_id,
      agent: session.agent_id,
    }
  );
}

export function sessionProject(session) {
  return session?.cwd ? session.cwd.split("/").filter(Boolean).pop() : null;
}

function sessionClient(session) {
  return session.tmux_session ? `tmux ${session.tmux_session}` : CLIENT_LABELS[session.entrypoint];
}

/** "placet-hook-lab · app desktop · <title>" for the session a request came from. */
export function sessionOrigin(session) {
  if (!session) return null;
  return [sessionProject(session), sessionClient(session), session.title].filter(Boolean).join(" · ") || null;
}

/** "placet-hook-lab · app desktop", under the session name. */
export function sessionPlace(session) {
  return [sessionProject(session), sessionClient(session)].filter(Boolean).join(" · ");
}

export function sessionName(session) {
  if (session.title) return session.title;
  if (session.cwd) return session.cwd.split("/").filter(Boolean).pop();
  return session.id.slice(0, 8);
}

export function sessionStatus(session) {
  if (session.ended_at) return { key: "offline", label: "Chiusa" };
  if (session.state === "busy") return { key: "busy", label: "Al lavoro" };
  if (session.reachable) return { key: "idle", label: "In attesa" };
  if (session.tmux_session) return { key: "idle", label: "In attesa (tmux)" };
  return { key: "offline", label: "Non raggiungibile" };
}

/** When the session last started or finished a turn. */
export function activityOf(session) {
  return session.activity_at || session.last_seen_at || session.started_at;
}

/** Most recently active first (RF-40). */
export function byActivity(a, b) {
  return new Date(activityOf(b)) - new Date(activityOf(a));
}

export function timeAgo(iso, now = Date.now()) {
  if (!iso) return "";
  const seconds = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "ora";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min fa`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h fa`;
  return new Date(iso).toLocaleDateString("it-IT");
}
