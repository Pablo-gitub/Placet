import React from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, ShieldCheck, Terminal } from "lucide-react";
import LanguageSelect from "./i18n/LanguageSelect";
import BackgroundLanguage from "./i18n/BackgroundLanguage";
import PushControl from "./PushControl";
import NotificationPreferences from "./NotificationPreferences";
import UsageGuard from "./UsageGuard";

function Row({ title, hint, children }) {
  return (
    <div className="settings-row">
      <div className="settings-row-text">
        <strong>{title}</strong>
        {hint && <small>{hint}</small>}
      </div>
      <div className="settings-row-control">{children}</div>
    </div>
  );
}

/** Settings: language, notifications, and the way to the history. */
export default function Settings({ onOpenHistory, onOpenCommands }) {
  const { t } = useTranslation();
  return (
    <div className="settings" data-testid="settings-section">
      <h2 className="settings-title">{t("settings.title")}</h2>

      <section className="card settings-group">
        <h3 className="section-label">{t("settings.language")}</h3>
        <Row title={t("language.label")} hint={t("settings.languageHint")}><LanguageSelect full /></Row>
        <Row title={t("backgroundLanguage.title")} hint={t("settings.backgroundHint")}><BackgroundLanguage /></Row>
      </section>

      <section className="card settings-group">
        <h3 className="section-label">{t("settings.notifications")}</h3>
        <Row title={t("push.title")} hint={t("settings.pushHint")}><PushControl /></Row>
        <NotificationPreferences />
      </section>

      <section className="card settings-group">
        <h3 className="section-label">{t("settings.automation")}</h3>
        <UsageGuard />
      </section>

      <section className="card settings-group">
        <button type="button" className="settings-link" data-testid="settings-commands" onClick={onOpenCommands}>
          <Terminal size={16} />
          <span className="settings-row-text">
            <strong>{t("commands.title")}</strong>
            <small>{t("commands.settingsHint")}</small>
          </span>
          <ChevronRight size={16} />
        </button>
      </section>

      <section className="card settings-group">
        <h3 className="section-label">{t("settings.records")}</h3>
        <button type="button" className="settings-link" data-testid="settings-history" onClick={onOpenHistory}>
          <ShieldCheck size={16} />
          <span className="settings-row-text">
            <strong>{t("app.history")}</strong>
            <small>{t("settings.historyHint")}</small>
          </span>
          <ChevronRight size={16} />
        </button>
      </section>
    </div>
  );
}
