import React, { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

const EVENT_LABELS = {
  created: "Creata",
  decided: "Decisa",
  injected: "Inviata al terminale",
  replied: "Risposta ricevuta",
  delivered: "Esito consegnato al mittente",
  delivery_failed: "Consegna dell'esito fallita",
  injection_claimed: "Invio al terminale",
  injection_released: "Invio non riuscito",
  injection_requeued: "Nessuna conferma, nuovo tentativo",
  injection_failed: "Consegna fallita",
  acked: "Ricezione confermata",
  delivery_uncertain: "Consegna incerta",
  delivery_retried: "Nuovo tentativo chiesto da te",
  execution_started: "Turno iniziato",
  execution_completed: "Turno concluso",
  execution_interrupted: "Turno interrotto",
  start_unconfirmed: "Avvio del turno non confermato",
  recipient_unreachable: "Sessione non raggiungibile: in attesa del suo prossimo turno",
  marked_finished_by_user: "Segnata da te come terminata",
  queue_overridden: "Prossimo messaggio accodato comunque",
  stalled: "Bloccata: nessuna risposta",
  cancelled: "Annullata",
  expired: "Scaduta",
};

function actorLabel(actor) {
  if (actor === "system") return "sistema";
  if (actor === "system:auto-routing") return "auto-routing";
  if (actor === "bridge") return "bridge";
  const [kind, id] = actor.split(/:(.*)/s);
  if (kind === "human") return `tu (${id})`;
  return (id || actor).replace(/:hook$/, " (hook)");
}

function detailText(event) {
  const d = event.details || {};
  const parts = [];
  if (d.status) parts.push(d.status);
  if (d.selected_options?.length) parts.push(d.selected_options.join(", "));
  if (d.custom_text) parts.push(`“${d.custom_text}”`);
  if (d.edited_prompt) parts.push("prompt modificato");
  if (d.via) parts.push(d.via === "api" ? "in attesa diretta" : `via ${d.via}`);
  if (d.attempt) parts.push(`tentativo ${d.attempt}`);
  if (d.attempts) parts.push(`${d.attempts} tentativi`);
  if (d.implicit) parts.push("risposta implicita");
  return parts.join(" · ");
}

/** Collapsible audit log of one request (RF-13), loaded on first open. */
export default function Timeline({ requestId }) {
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
        Cronologia
      </button>
      {open && (
        <ol className="timeline-list" data-testid={`timeline-${requestId}`}>
          {error && <li className="timeline-error">Impossibile caricare la cronologia.</li>}
          {!error && events === null && <li>Caricamento…</li>}
          {events?.map((ev) => (
            <li key={ev.id}>
              <time dateTime={ev.at}>{new Date(ev.at).toLocaleString()}</time>
              <span className="timeline-event">{EVENT_LABELS[ev.event] || ev.event}</span>
              <span className="timeline-actor">{actorLabel(ev.actor)}</span>
              {detailText(ev) && <span className="timeline-detail">{detailText(ev)}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
