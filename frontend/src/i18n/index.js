import i18next from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import it from "./locales/it.json";
import { LANGUAGE_KEY, readPreference, resolveLanguage, SUPPORTED_LANGUAGES } from "./locale";

function storage() {
  try { return window.localStorage; } catch { return null; }
}

export function browserLanguages() {
  return typeof navigator === "undefined" ? [] : navigator.languages || [navigator.language];
}

let preference = readPreference(storage());

void i18next.use(initReactI18next).init({
  resources: { en: { translation: en }, it: { translation: it } },
  lng: resolveLanguage(preference, browserLanguages()),
  fallbackLng: "en",
  supportedLngs: SUPPORTED_LANGUAGES,
  interpolation: { escapeValue: false }, // React escapes rendered text.
  initAsync: false,
});

function updateDocument(language) {
  if (typeof document === "undefined") return;
  document.documentElement.lang = language;
  document.title = i18next.t("app.title");
}

i18next.on("languageChanged", updateDocument);
updateDocument(i18next.resolvedLanguage || "en");

export function getLanguagePreference() { return preference; }

export async function setLanguagePreference(value) {
  preference = SUPPORTED_LANGUAGES.includes(value) ? value : "auto";
  try {
    if (preference === "auto") storage()?.removeItem(LANGUAGE_KEY);
    else storage()?.setItem(LANGUAGE_KEY, preference);
  } catch { /* The in-memory preference still works in restricted storage. */ }
  await i18next.changeLanguage(resolveLanguage(preference, browserLanguages()));
}

if (typeof window !== "undefined") {
  window.addEventListener("languagechange", () => {
    if (preference === "auto") void setLanguagePreference("auto");
  });
}

export default i18next;
