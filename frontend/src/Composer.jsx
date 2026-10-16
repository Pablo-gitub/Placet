import React, { useRef, useState } from "react";
import { providerOf, sessionName, sessionProject, sessionStatus } from "./sessionInfo";
import { SendHorizontal } from "lucide-react";

/** Writing to a session from the phone (RF-34): the message wakes it. */
export default function Composer({ session, sessions = [], onSend }) {
  const [text, setText] = useState("");
  const input = useRef(null);
  const [mentions, setMentions] = useState([]);
  const [query, setQuery] = useState(null);
  const [highlight, setHighlight] = useState(0);
  const updateQuery = (value, caret) => {
    const match = value.slice(0, caret).match(/(?:^|\s)@([^@\n]*)$/);
    setQuery(match ? { start: caret - match[1].length - 1, end: caret, text: match[1] } : null);
    setHighlight(0);
  };
  const candidates = sessions.filter((s) => s.id !== session?.id && !s.ended_at && !s.excluded)
    .filter((s) => !query?.text || `${providerOf(s).label} ${s.agent_id} ${s.cwd || ""} ${sessionName(s)} ${s.id}`
      .toLocaleLowerCase().includes(query.text.trim().toLocaleLowerCase()));
  const activeMentions = mentions.filter((m) => text.includes(m.token));
  const choose = (target) => {
    const token = `@${providerOf(target).label} · ${sessionProject(target) || "senza progetto"} · ${sessionName(target)} [${target.id.slice(0, 8)}]`;
    const next = text.slice(0, query.start) + token + " " + text.slice(query.end);
    const caret = query.start + token.length + 1;
    setText(next);
    setMentions((prev) => [...prev.filter((m) => m.session_id !== target.id), { session_id: target.id, token }]);
    setQuery(null);
    requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(caret, caret); });
  };
  const [sending, setSending] = useState(false);
  const closed = !!session?.ended_at;
  const unreachable = session && !closed && !session.reachable && !session.tmux_session;

  const send = async () => {
    const message = text.trim();
    if (!message || sending || closed) return;
    setSending(true);
    try {
      if (await onSend(message, activeMentions)) { setText(""); setMentions([]); setQuery(null); }
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
      {activeMentions.length > 0 && (
        <p className="composer-note" role="status">Riferimenti: {activeMentions.length === 1 ? "una sessione" : `${activeMentions.length} sessioni`}. Scrivi alla sessione corrente: le richieste agli altri agenti passeranno da Placet.</p>
      )}
      {query && !closed && (
        <div className="mention-picker" id="session-mentions" role="listbox" aria-label="Sessioni da menzionare">
          {candidates.length === 0 && <p className="composer-note">Nessuna sessione trovata.</p>}
          {candidates.map((target, i) => (
            <button type="button" role="option" aria-selected={i === highlight} id={`mention-${i}`}
              key={target.id} onMouseDown={(e) => e.preventDefault()} onClick={() => choose(target)}>
              <strong>{providerOf(target).label} · {sessionName(target)}</strong>
              <span>{target.cwd || "Senza progetto"} · {sessionStatus(target).label} · {target.id.slice(0, 8)}</span>
            </button>
          ))}
        </div>
      )}
      <div className="composer-row">
        <textarea
          ref={input}
          aria-label="Messaggio per la sessione"
          aria-controls={query ? "session-mentions" : undefined}
          aria-expanded={!!query}
          aria-activedescendant={query && candidates[highlight] ? `mention-${highlight}` : undefined}
          data-testid="composer-input"
          placeholder={closed ? "Sessione chiusa" : "Scrivi alla sessione… @ per citarne un’altra"}
          value={text}
          rows={1}
          disabled={closed}
          onChange={(e) => { setText(e.target.value); updateQuery(e.target.value, e.target.selectionStart); }}
          onClick={(e) => updateQuery(text, e.target.selectionStart)}
          onKeyDown={(e) => {
            if (query) {
              if (e.key === "Escape") { e.preventDefault(); setQuery(null); return; }
              if (["ArrowDown", "ArrowUp"].includes(e.key)) {
                e.preventDefault();
                setHighlight((i) => Math.max(0, Math.min(candidates.length - 1, i + (e.key === "ArrowDown" ? 1 : -1))));
                return;
              }
              if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && candidates[highlight]) {
                e.preventDefault(); choose(candidates[highlight]); return;
              }
            }
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
