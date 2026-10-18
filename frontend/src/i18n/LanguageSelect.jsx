import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { getLanguagePreference, setLanguagePreference } from "./index";

export default function LanguageSelect() {
  const { t } = useTranslation();
  const [preference, setPreference] = useState(getLanguagePreference);
  const change = async (event) => {
    const value = event.target.value;
    setPreference(value);
    await setLanguagePreference(value);
  };
  return <select className="language-select" aria-label={t("language.label")}
    value={preference} onChange={change}>
    <option value="auto">{t("language.auto")}</option>
    <option value="en">English</option>
    <option value="it">Italiano</option>
  </select>;
}
