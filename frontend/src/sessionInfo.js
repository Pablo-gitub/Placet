import i18n from "./i18n";
import { formatDateTime, formatRelativeTime } from "./i18n/formatters";
/** Display helpers for agent sessions. */

const CLIENT_LABELS = {
  "claude-desktop": "sessions.desktop",
  "codex-desktop": "sessions.desktop",
  cli: "sessions.terminal",
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
  // The project, not the folder the session happens to work in (a worktree's root is its repository).
  const folder = session?.project_root || session?.cwd;
  return folder ? folder.split("/").filter(Boolean).pop() : null;
}

function sessionClient(session) {
  return session.tmux_session ? `tmux ${session.tmux_session}` : (CLIENT_LABELS[session.entrypoint] ? i18n.t(CLIENT_LABELS[session.entrypoint]) : undefined);
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

/** A session's title; untitled ones get their project and opening time, to tell them apart. */
export function sessionName(session) {
  if (session.title) return session.title;
  const opened = session.started_at
    ? formatDateTime(session.started_at, {
        day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
      })
    : null;
  const project = sessionProject(session);
  if (project) return opened ? i18n.t("sessions.opened", { project, time: opened }) : project;
  return session.id.slice(0, 8);
}

/**
 * RF-29d: Maps session ID to disambiguated label. Sessions sharing a title
 * receive an ID suffix [id.slice(0, 8)] to tell them apart.
 */
export function sessionLabels(sessions = []) {
  const counts = new Map();
  for (const s of sessions) {
    if (!s) continue;
    const base = sessionName(s);
    counts.set(base, (counts.get(base) || 0) + 1);
  }
  const result = {};
  for (const s of sessions) {
    if (!s) continue;
    const base = sessionName(s);
    if ((counts.get(base) || 0) > 1 && s.id) {
      result[s.id] = `${base} [${s.id.slice(0, 8)}]`;
    } else if (s.id) {
      result[s.id] = base;
    }
  }
  return result;
}

/** The CSS class that gives a session its provider's colours. */
export function providerClass(session) {
  return `prov-${providerOf(session).key}`;
}

export function sessionStatus(session) {
  if (session.ended_at) return { key: "offline", label: i18n.t("sessions.closed") };
  if (session.suspended_at) return { key: "offline", label: i18n.t("sessions.suspended") };
  if (session.state === "busy") return { key: "busy", label: i18n.t("sessions.busy") };
  if (session.reachable) return { key: "idle", label: i18n.t("sessions.idle") };
  if (session.tmux_session) return { key: "idle", label: i18n.t("sessions.tmux") };
  return { key: "offline", label: i18n.t("sessions.unreachable") };
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
  if (seconds < 60) return formatRelativeTime(0, "second");
  if (seconds < 3600) return formatRelativeTime(-Math.floor(seconds / 60), "minute");
  if (seconds < 86400) return formatRelativeTime(-Math.floor(seconds / 3600), "hour");
  return formatDateTime(iso);
}
