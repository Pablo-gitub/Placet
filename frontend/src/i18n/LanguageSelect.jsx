import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { getLanguagePreference, setLanguagePreference } from "./index";

/** `full`: names next to the flags (Settings); otherwise flags only. */
export default function LanguageSelect({ full = false }) {
  const { t } = useTranslation();
  const [preference, setPreference] = useState(getLanguagePreference);
  const change = async (event) => {
    const value = event.target.value;
    setPreference(value);
    await setLanguagePreference(value);
  };
  return <select className="language-select" aria-label={t("language.label")} title={t("language.label")}
    value={preference} onChange={change}>
    <option value="auto" title={t("language.auto")}>{full ? `🌐 ${t("language.auto")}` : "🌐"}</option>
    <option value="en" title="English">{full ? "🇬🇧 English" : "🇬🇧"}</option>
    <option value="it" title="Italiano">{full ? "🇮🇹 Italiano" : "🇮🇹"}</option>
  </select>;
}
