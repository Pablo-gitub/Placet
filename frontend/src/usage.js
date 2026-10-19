// RF-60: usage windows per provider, shaped for the home bar.
import { PROVIDERS } from "./sessionInfo";
import { formatDateTime } from "./i18n/formatters";

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

// Providers asked periodically: old data means the reading failed, not that
// nothing was used (Claude needs a valid Claude Code login on this PC).
const POLLED = new Set(["claude-code", "antigravity"]);
export const STALE_MS = 20 * 60 * 1000;

/**
 * The rows to show: one per provider, or one per model group when a provider
 * has several (Antigravity: Gemini, and Claude/GPT). A window whose reset time
 * has passed counts as unused, unless the provider's data is stale: then the
 * value is unknown (`used: null`), since usage after the reset was not read.
 */
export function usageRows(providers, now = Date.now()) {
  const rows = [];
  // Never let a malformed answer break the home: no rows instead.
  const list = Array.isArray(providers) ? [...providers] : [];
  // Same order as the session groups: Claude, Codex, Antigravity.
  list.sort((a, b) => order(a?.provider) - order(b?.provider));
  for (const usage of list) {
    const buckets = Array.isArray(usage?.buckets) ? usage.buckets : [];
    const observed = usage?.observed_at ? new Date(usage.observed_at).getTime() : null;
    const stale = POLLED.has(usage?.provider) && observed !== null && now - observed > STALE_MS;
    const groups = [...new Set(buckets.map((b) => b.group || ""))];
    for (const group of groups) {
      const windows = {};
      for (const b of buckets.filter((x) => (x.group || "") === group)) {
        const expired = b.resets_at && new Date(b.resets_at).getTime() <= now;
        windows[b.window] = expired
          ? { used: stale ? null : 0, resetsAt: null }
          : { used: b.used_percent, resetsAt: b.resets_at };
      }
      rows.push({
        key: `${usage.provider}:${group}`,
        provider: usage.provider,
        group: group.replace(/\s+models$/i, ""),
        windows,
        staleSince: stale ? usage.observed_at : null,
      });
    }
  }
  return rows;
}

/**
 * When a provider at its usage limit can take messages again: the latest reset
 * among its used-up account-wide windows, or null. Mirrors the backend, which
 * holds that provider's messages until then.
 */
export function limitReset(providers, provider, now = Date.now()) {
  const usage = (Array.isArray(providers) ? providers : []).find((u) => u?.provider === provider);
  const resets = (Array.isArray(usage?.buckets) ? usage.buckets : [])
    .filter((b) => !b.group && b.used_percent >= 100 && b.resets_at && new Date(b.resets_at).getTime() > now)
    .map((b) => b.resets_at)
    .sort();
  return resets.length ? resets[resets.length - 1] : null;
}

/** A reset time as a clock time, with the day when it is not within the next day. */
export function resetClock(iso, now = Date.now()) {
  const soon = new Date(iso).getTime() - now < 20 * 3600 * 1000;
  return formatDateTime(iso, soon ? { hour: "2-digit", minute: "2-digit" } : { weekday: "short", hour: "2-digit", minute: "2-digit" });
}
