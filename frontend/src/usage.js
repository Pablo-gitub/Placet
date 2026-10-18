// RF-60: usage windows per provider, shaped for the home bar.
import { PROVIDERS } from "./sessionInfo";

const order = (provider) => {
  const i = PROVIDERS.findIndex((p) => p.key === provider);
  return i < 0 ? PROVIDERS.length : i;
};

/** Time left until a reset, compact: "45 min", "1h20", "3d 5h". */
export function untilReset(iso, t, now = Date.now()) {
  if (!iso) return null;
  const minutes = Math.max(0, Math.round((new Date(iso).getTime() - now) / 60000));
  if (minutes < 60) return t("usage.minutes", { m: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("usage.hoursMinutes", { h: hours, m: String(minutes % 60).padStart(2, "0") });
  return t("usage.daysHours", { d: Math.floor(hours / 24), h: hours % 24 });
}

/**
 * The rows to show: one per provider, or one per model group when a provider
 * has several (Antigravity: Gemini, and Claude/GPT). A window whose reset time
 * has passed counts as unused: the data may predate the reset.
 */
export function usageRows(providers, now = Date.now()) {
  const rows = [];
  // Never let a malformed answer break the home: no rows instead.
  const list = Array.isArray(providers) ? [...providers] : [];
  // Same order as the session groups: Claude, Codex, Antigravity.
  list.sort((a, b) => order(a?.provider) - order(b?.provider));
  for (const usage of list) {
    const buckets = Array.isArray(usage?.buckets) ? usage.buckets : [];
    const groups = [...new Set(buckets.map((b) => b.group || ""))];
    for (const group of groups) {
      const windows = {};
      for (const b of buckets.filter((x) => (x.group || "") === group)) {
        const expired = b.resets_at && new Date(b.resets_at).getTime() <= now;
        windows[b.window] = { used: expired ? 0 : b.used_percent, resetsAt: expired ? null : b.resets_at };
      }
      rows.push({
        key: `${usage.provider}:${group}`,
        provider: usage.provider,
        group: group.replace(/\s+models$/i, ""),
        windows,
      });
    }
  }
  return rows;
}
