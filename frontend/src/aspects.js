// The three aspects of an agent request (RF-29), as the PWA names them.
export const DELIVERY = {
  waiting: "in attesa",
  in_progress: "in corso",
  accepted: "accettata",
  uncertain: "incerta",
  failed: "fallita",
  cancelled: "annullata",
};
export const EXECUTION = {
  unobserved: "non osservata",
  started: "iniziata",
  completed: "completata",
  interrupted: "interrotta",
};
export const REPLY = {
  none: "nessuna",
  explicit: "esplicita",
  implicit: "implicita (ultima risposta del turno)",
  ambiguous: "ambigua (nel turno c'erano altri messaggi)",
};

export const isUncertain = (req) => req.status === "DELIVERING" && req.delivery === "uncertain";

/**
 * A delivered request still holding its session's queue whose end Placet
 * cannot see: the turn was not seen starting in time, or it is stalled.
 * Only the user moves the queue on (RF-29b).
 */
export const holdsQueueUnseen = (req) =>
  Boolean(req.recipient_session_id) &&
  !req.queue_released_at &&
  ["EXECUTING", "STALLED", "COMPLETED"].includes(req.status) &&
  ["unobserved", "started"].includes(req.execution) &&
  (Boolean(req.start_unconfirmed_at) || req.status === "STALLED");
