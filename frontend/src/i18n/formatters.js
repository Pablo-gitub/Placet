import i18n, { browserLanguages } from "./index";
import { supportedLanguage } from "./locale";

export function formattingLocale() {
  const language = i18n.resolvedLanguage || "en";
  for (const value of browserLanguages()) {
    if (supportedLanguage(value) !== language) continue;
    try { return Intl.getCanonicalLocales(value)[0]; } catch { /* Ignore malformed browser values. */ }
  }
  return language;
}

export const formatDateTime = (value, options) => new Intl.DateTimeFormat(formattingLocale(), options).format(new Date(value));
export const formatNumber = (value, options) => new Intl.NumberFormat(formattingLocale(), options).format(value);
export const formatRelativeTime = (value, unit) => new Intl.RelativeTimeFormat(formattingLocale(), { numeric: "auto" }).format(value, unit);
export const formatWhen = (value) => formatDateTime(value, { dateStyle: "short", timeStyle: "short" });
