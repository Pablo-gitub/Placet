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
