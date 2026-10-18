export const LANGUAGE_KEY = "placet.language";
export const SUPPORTED_LANGUAGES = ["en", "it"];

export function supportedLanguage(value) {
  if (typeof value !== "string") return null;
  const base = value.trim().toLowerCase().split(/[-_]/)[0];
  return SUPPORTED_LANGUAGES.includes(base) ? base : null;
}

export function resolveLanguage(preference, languages = []) {
  return supportedLanguage(preference) || languages.map(supportedLanguage).find(Boolean) || "en";
}

export function readPreference(storage) {
  try {
    const value = storage.getItem(LANGUAGE_KEY);
    return SUPPORTED_LANGUAGES.includes(value) ? value : "auto";
  } catch {
    return "auto";
  }
}
