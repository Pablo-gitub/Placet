import React, { useEffect, useRef, useState } from "react";
import { providerOf, sessionName, sessionProject, sessionStatus } from "./sessionInfo";
import { Paperclip, SendHorizontal, X } from "lucide-react";

const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];

function FilePreview({ file, onRemove, disabled }) {
  const image = useRef(null);
  useEffect(() => {
    if (!file.type.startsWith("image/") || !URL.createObjectURL) return;
    const source = URL.createObjectURL(file);
    if (image.current) image.current.src = source;
    return () => URL.revokeObjectURL(source);
  }, [file]);
  return <li className="composer-file">
    {file.type.startsWith("image/") && <img ref={image} alt="" />}
    <span>{file.name}<small>{file.size < 1024 * 1024 ? `${Math.max(1, Math.ceil(file.size / 1024))} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`}</small></span>
    <button type="button" className="icon-button" aria-label={`Rimuovi ${file.name}`} disabled={disabled} onClick={onRemove}><X size={16} /></button>
  </li>;
}

function encodeFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name.slice(0, 200), data: String(reader.result).split(",")[1] });
    reader.onerror = () => reject(new Error("Non riesco a leggere l’allegato. Selezionalo nuovamente."));
    reader.onabort = reader.onerror;
    reader.readAsDataURL(file);
  });
}

/** Writing to a session from the phone (RF-34): the message wakes it. */
const MODE_KEY = "placet.sendMode";
// Providers whose running turn can take a message "now" (RF-35).
const STEER_PROVIDERS = ["claude-code", "antigravity"];

function savedMode() {
  try {
    return localStorage.getItem(MODE_KEY) === "now" ? "now" : "queue";
  } catch {
    return "queue";
  }
}

export default function Composer({ session, sessions = [], onSend, draft }) {
  // "In coda" (end of the turn) or "Subito" (inside the running turn): the
  // last choice is the default on this device (RF-35).
  const [mode, setModeState] = useState(savedMode);
  const setMode = (value) => {
    setModeState(value);
    try {
      localStorage.setItem(MODE_KEY, value);
    } catch {
      // private mode: the choice lasts for this page
    }
  };
  // A withdrawn message to correct (Modifica) starts the composer again with
  // its text; its files must be picked again.
  const [text, setText] = useState(draft?.text || "");
  const input = useRef(null);
  const fileInput = useRef(null);
  const [files, setFiles] = useState([]);
  const [fileError, setFileError] = useState("");
  const selectFiles = (event) => {
    const selected = [...files, ...Array.from(event.target.files || [])];
    event.target.value = "";
    if (selected.length > 4) { setFileError("Puoi allegare al massimo 4 file."); return; }
    if (selected.some((f) => !ALLOWED_TYPES.includes(f.type) && !((!f.type || f.type === "application/octet-stream") && /\.(pdf|png|jpe?g|webp)$/i.test(f.name)))) {
      setFileError("Scegli immagini JPEG, PNG, WebP o PDF. Per una foto HEIC, esportala prima in JPEG."); return;
    }
    if (selected.some((f) => f.size > MAX_FILE_BYTES) || selected.reduce((n, f) => n + f.size, 0) > MAX_TOTAL_BYTES) {
      setFileError("Limite di 10 MB per file e 20 MB complessivi."); return;
    }
    setFiles(selected); setFileError("");
  };
  const [mentions, setMentions] = useState(draft?.mentions || []);
  const [refiles, setRefiles] = useState(draft?.files || []);
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
  const suspended = !closed && !!session?.suspended_at;
  const unreachable = session && !closed && !suspended && !session.reachable && !session.tmux_session;
  const busy = session?.state === "busy" && !closed;
  const canSteer = STEER_PROVIDERS.includes(session?.provider);

  const send = async () => {
    const message = text.trim();
    if ((!message && !files.length) || sending || closed) return;
    setSending(true);
    setFileError("");
    try {
      const attachments = await Promise.all(files.map(encodeFile));
      const now = busy && canSteer && mode === "now";
      if (await onSend(message, activeMentions, attachments, now ? "now" : "queue")) {
        setText(""); setMentions([]); setQuery(null); setFiles([]); setRefiles([]);
      }
    } catch (error) {
      setFileError(error.message || "Trasferimento non riuscito: puoi riprovare.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="composer" data-testid="composer">
      {closed && <p className="composer-note">La sessione è chiusa: non puoi più scriverle.</p>}
      {suspended && (
        <p className="composer-note" data-testid="composer-suspended">
          {session.provider === "claude-code"
            ? "Claude ha sospeso la conversazione. Quando invii, Placet prova a riaprirla; il messaggio resta in attesa finché torna raggiungibile."
            : "Codex ha sospeso la conversazione. Quando invii, Placet accoda il messaggio e prova a riaprirla nell’app."}
        </p>
      )}
      {refiles.length > 0 && (
        <p className="composer-note" data-testid="composer-refiles">
          Allegati da scegliere di nuovo: {refiles.join(", ")}
        </p>
      )}
      {unreachable && (
        <p className="composer-note" data-testid="composer-unreachable">
          {session.provider === "claude-code" && session.entrypoint === "claude-desktop"
            ? "Quando invii, Placet prova a riaprire la conversazione in Claude. Se resta non raggiungibile, aprila nell’app: il messaggio rimane in attesa."
            : "La sessione non è raggiungibile ora: il messaggio partirà quando torna attiva."}
        </p>
      )}
      {busy && (
        <div className="send-mode" role="radiogroup" aria-label="Quando consegnare" data-testid="send-mode">
          <span className="composer-note">Sta lavorando:</span>
          <button type="button" role="radio" aria-checked={mode !== "now" || !canSteer}
            className={mode !== "now" || !canSteer ? "active" : ""} data-testid="mode-queue"
            onClick={() => setMode("queue")}>
            In coda
          </button>
          <button type="button" role="radio" aria-checked={mode === "now" && canSteer} disabled={!canSteer}
            className={mode === "now" && canSteer ? "active" : ""} data-testid="mode-now"
            title={canSteer ? "Entra nel turno in corso, dopo la prossima azione" : "Non ancora disponibile per Codex"}
            onClick={() => setMode("now")}>
            Subito
          </button>
          <span className="composer-note send-mode-hint">
            {mode === "now" && canSteer
              ? "entra nel turno in corso, dopo la prossima azione."
              : canSteer ? "arriva a fine turno." : "arriva a fine turno (Subito non ancora disponibile per Codex)."}
          </span>
        </div>
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
      {fileError && <p className="composer-note" role="alert">{fileError}</p>}
      {files.length > 0 && <>
        <ul className="composer-files" aria-label="Allegati da inviare">
          {files.map((file, i) => <FilePreview key={`${file.name}-${i}`} file={file} disabled={sending}
            onRemove={() => { setFiles((prev) => prev.filter((_, n) => n !== i)); setFileError(""); }} />)}
        </ul>
        <p className="composer-note" role="status">{sending ? "Trasferimento al PC e invio in corso…" : "Gli allegati saranno salvati sul PC prima dell’invio. Max 4 file, 10 MB ciascuno, 20 MB totali."}</p>
      </>}
      <input ref={fileInput} type="file" hidden multiple accept={ALLOWED_TYPES.join(",")}
        aria-label="Scegli allegati" disabled={closed || sending} onChange={selectFiles} />
      <div className="composer-row">
        <button type="button" className="icon-button attach-button" aria-label="Allega foto o PDF"
          disabled={closed || sending} onClick={() => fileInput.current?.click()}><Paperclip size={18} /></button>
        <textarea autoFocus={!!draft}
          ref={input}
          aria-label="Messaggio per la sessione"
          aria-controls={query ? "session-mentions" : undefined}
          aria-expanded={!!query}
          aria-activedescendant={query && candidates[highlight] ? `mention-${highlight}` : undefined}
          data-testid="composer-input"
          placeholder={closed ? "Sessione chiusa" : "Scrivi alla sessione… @ per citarne un’altra"}
          value={text}
          rows={1}
          disabled={closed || sending}
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
          disabled={closed || sending || (!text.trim() && !files.length)}
          onClick={send}
        >
          <SendHorizontal size={18} />
        </button>
      </div>
    </div>
  );
}
