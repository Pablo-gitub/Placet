import i18n from "./i18n";
import { formatDateTime } from "./i18n/formatters";
/**
 * Helpers for the entries of a session's conversation, as returned by
 * GET /api/sessions/{id}/transcript (read by the bridge, RF-30/RF-32).
 */

const basename = (path) => (path ? String(path).split("/").filter(Boolean).pop() || path : "");

/** One readable line for a tool call. */
export function actionLabel(entry) {
  const detail = entry.detail || "";
  switch (entry.kind) {
    case "command":
      return `$ ${detail}`;
    case "read":
      return i18n.t("actions.read", { name: basename(detail) });
    case "edit":
      return i18n.t("actions.edit", { name: basename(detail) });
    case "write":
      return i18n.t("actions.write", { name: basename(detail) });
    case "search":
      return i18n.t("actions.search", { detail });
    case "web":
      return i18n.t("actions.web", { detail });
    case "mcp":
      return detail ? `${entry.tool} (${detail})` : entry.tool;
    case "skill":
      return i18n.t("actions.skill", { detail });
    case "agent":
      return i18n.t("actions.agent", { detail });
    case "tools":
      return i18n.t("actions.tools");
    case "plan":
      return i18n.t("actions.plan");
    default:
      return detail ? `${entry.tool}: ${detail}` : entry.tool || i18n.t("actions.fallback");
  }
}

/** Consecutive tool calls become one group, so the conversation stays readable. */
export function groupEntries(entries) {
  const items = [];
  for (const entry of entries) {
    const last = items[items.length - 1];
    if (entry.role === "action") {
      if (last?.type === "actions") last.entries.push(entry);
      else items.push({ type: "actions", key: entry.cursor, entries: [entry] });
    } else {
      items.push({ type: "entry", key: entry.cursor, entry });
    }
  }
  return items;
}

/** Joins pages without duplicates (a cursor identifies an entry). */
export function mergeEntries(first, second) {
  const seen = new Set(first.map((e) => e.cursor));
  return [...first, ...second.filter((e) => !seen.has(e.cursor))];
}

export function timeOf(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return formatDateTime(date, { hour: "2-digit", minute: "2-digit" });
}
