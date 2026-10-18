import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Languages, X } from "lucide-react";

function LanguageDialog({ onClose }) {
  const { t } = useTranslation();
  const [language, setLanguage] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetch("/api/settings/language")
      .then(async (response) => {
        if (!response.ok) throw new Error("Unable to load background language");
        const body = await response.json();
        if (!["en", "it"].includes(body.language)) throw new Error("Invalid background language");
        if (!cancelled) setLanguage(body.language);
      })
      .catch(() => { if (!cancelled) setError("backgroundLanguage.loadError"); });
    return () => { cancelled = true; };
  }, []);

  const save = async () => {
    setSaving(true); setError(null); setSaved(false);
    try {
      const response = await fetch("/api/settings/language", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ language }),
      });
      if (!response.ok) throw new Error("Unable to save background language");
      const body = await response.json();
      if (body.language !== language) throw new Error("Language was not saved");
      setSaved(true);
    } catch { setError("backgroundLanguage.saveError"); }
    finally { setSaving(false); }
  };
  const close = () => { if (!saving) onClose(); };
  return createPortal(
    <div className="dialog-backdrop" onClick={close}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label={t("backgroundLanguage.title")} onClick={e => e.stopPropagation()}>
        <div className="dialog-header">
          <h2>{t("backgroundLanguage.title")}</h2>
          <button type="button" className="icon-button" aria-label={t("picker.close")} disabled={saving} onClick={close}><X size={18} /></button>
        </div>
        <div className="dialog-body">
          <p>{t("backgroundLanguage.explanation")}</p>
          {language === null && !error && <p role="status">{t("backgroundLanguage.loading")}</p>}
          {language !== null && <>
            <label className="prompt-editor-label" htmlFor="background-language">{t("backgroundLanguage.label")}</label>
            <select id="background-language" className="session-select" value={language} disabled={saving}
              onChange={e => { setLanguage(e.target.value); setSaved(false); }}>
              <option value="en">English</option><option value="it">Italiano</option>
            </select>
            <button type="button" className="btn btn-primary" disabled={saving} onClick={save}>
              {t(saving ? "backgroundLanguage.saving" : "backgroundLanguage.save")}
            </button>
          </>}
          {error && <p role="alert">{t(error)}</p>}
          {saved && <p role="status">{t("backgroundLanguage.saved")}</p>}
        </div>
      </div>
    </div>, document.body,
  );
}

export default function BackgroundLanguage() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className="icon-button" aria-label={t("backgroundLanguage.title")} title={t("backgroundLanguage.title")} onClick={() => setOpen(true)}>
      <Languages size={16} />
    </button>
    {open && <LanguageDialog onClose={() => setOpen(false)} />}
  </>;
}
