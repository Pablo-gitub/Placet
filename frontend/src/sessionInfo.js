/** Display helpers for agent sessions. */

const CLIENT_LABELS = {
  "claude-desktop": "app desktop",
  cli: "terminale",
};

/** "placet-hook-lab · app desktop · <title>" for the session a request came from. */
export function sessionOrigin(session) {
  if (!session) return null;
  const project = session.cwd ? session.cwd.split("/").filter(Boolean).pop() : null;
  const client = session.tmux_session ? `tmux ${session.tmux_session}` : CLIENT_LABELS[session.entrypoint];
  return [project, client, session.title].filter(Boolean).join(" · ") || null;
}

export function sessionName(session) {
  if (session.title) return session.title;
  if (session.cwd) return session.cwd.split("/").filter(Boolean).pop();
  return session.id.slice(0, 8);
}

export function sessionStatus(session) {
  if (session.state === "busy") return { key: "busy", label: "Al lavoro" };
  if (session.reachable) return { key: "idle", label: "In attesa" };
  if (session.tmux_session) return { key: "idle", label: "In attesa (tmux)" };
  return { key: "offline", label: "Non raggiungibile" };
}
