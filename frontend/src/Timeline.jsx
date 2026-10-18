import { useTranslation } from "react-i18next";
import i18n from "./i18n";
import { formatDateTime } from "./i18n/formatters";
import React, { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

function actorLabel(actor) {
  if (actor === "system") return i18n.t("timeline.system");
  if (actor === "system:auto-routing") return "auto-routing";
  if (actor === "bridge") return "bridge";
  const [kind, id] = actor.split(/:(.*)/s);
  if (kind === "human") return i18n.t("timeline.you", { id });
  return (id || actor).replace(/:hook$/, " (hook)");
}

function detailText(event) {
  const d = event.details || {};
  const parts = [];
  if (d.status) parts.push(d.status);
  if (d.selected_options?.length) parts.push(d.selected_options.join(", "));
  if (d.custom_text) parts.push(`“${d.custom_text}”`);
  if (d.edited_prompt) parts.push(i18n.t("timeline.edited"));
  if (d.via) parts.push(d.via === "api" ? i18n.t("timeline.direct") : i18n.t("timeline.via", { via: d.via }));
  if (d.attempt) parts.push(i18n.t("timeline.attempt", { count: d.attempt }));
  if (d.attempts) parts.push(i18n.t("timeline.attempts", { count: d.attempts }));
  if (d.implicit) parts.push(i18n.t("timeline.implicit"));
  return parts.join(" · ");
}

/** Collapsible audit log of one request (RF-13), loaded on first open. */
export default function Timeline({ requestId }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(false);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (!next) return;
    try {
      const res = await fetch(`/api/requests/${requestId}/events`);
      if (!res.ok) throw new Error(String(res.status));
      setEvents(await res.json());
      setError(false);
    } catch {
      setError(true);
    }
  };

  return (
    <div className="timeline">
      <button
        type="button"
        className="timeline-toggle"
        data-testid={`timeline-toggle-${requestId}`}
        aria-expanded={open}
        onClick={toggle}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {t("timeline.title")}
      </button>
      {open && (
        <ol className="timeline-list" data-testid={`timeline-${requestId}`}>
          {error && <li className="timeline-error">{t("timeline.error")}</li>}
          {!error && events === null && <li>{t("timeline.loading")}</li>}
          {events?.map((ev) => (
            <li key={ev.id}>
              <time dateTime={ev.at}>{formatDateTime(ev.at)}</time>
              <span className="timeline-event">{t(`timeline.events.${ev.event}`, { defaultValue: ev.event })}</span>
              <span className="timeline-actor">{actorLabel(ev.actor)}</span>
              {detailText(ev) && <span className="timeline-detail">{detailText(ev)}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
