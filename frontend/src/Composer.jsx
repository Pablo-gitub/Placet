import React, { useState } from "react";
import { SendHorizontal } from "lucide-react";

/** Writing to a session from the phone (RF-34): the message wakes it. */
export default function Composer({ session, onSend }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const closed = !!session?.ended_at;
  const unreachable = session && !closed && !session.reachable && !session.tmux_session;

  const send = async () => {
    const message = text.trim();
    if (!message || sending || closed) return;
    setSending(true);
    try {
      if (await onSend(message)) setText("");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="composer" data-testid="composer">
      {closed && <p className="composer-note">La sessione è chiusa: non puoi più scriverle.</p>}
      {unreachable && (
        <p className="composer-note" data-testid="composer-unreachable">
          La sessione non è raggiungibile ora: il messaggio partirà quando torna attiva.
        </p>
      )}
      {session?.state === "busy" && !closed && (
        <p className="composer-note">Sta lavorando: il messaggio arriverà a fine turno.</p>
      )}
      <div className="composer-row">
        <textarea
          aria-label="Messaggio per la sessione"
          data-testid="composer-input"
          placeholder={closed ? "Sessione chiusa" : "Scrivi alla sessione…"}
          value={text}
          rows={1}
          disabled={closed}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
          }}
        />
        <button
          type="button"
          className="icon-button send-button"
          aria-label="Invia"
          data-testid="composer-send"
          disabled={closed || sending || !text.trim()}
          onClick={send}
        >
          <SendHorizontal size={18} />
        </button>
      </div>
    </div>
  );
}
