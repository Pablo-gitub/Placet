/** Display helpers for agent sessions. */

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
