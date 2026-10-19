import { useTranslation } from "react-i18next";
import { formatNumber, formattingLocale } from "./i18n/formatters";
import React, { useEffect, useRef, useState } from "react";
import { providerOf, sessionLabels, sessionName, sessionProject, sessionStatus } from "./sessionInfo";
import { resetClock } from "./usage";
import { Paperclip, SendHorizontal, X } from "lucide-react";

const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];

function FilePreview({ file, onRemove, disabled }) {
  const { t } = useTranslation();
  const image = useRef(null);
  useEffect(() => {
    if (!file.type.startsWith("image/") || !URL.createObjectURL) return;
    const source = URL.createObjectURL(file);
    if (image.current) image.current.src = source;
    return () => URL.revokeObjectURL(source);
  }, [file]);
  return <li className="composer-file">
    {file.type.startsWith("image/") && <img ref={image} alt="" />}
    <span>{file.name}<small>{file.size < 1024 * 1024 ? `${formatNumber(Math.max(1, Math.ceil(file.size / 1024)))} KB` : `${formatNumber(file.size / 1024 / 1024, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`}</small></span>
    <button type="button" className="icon-button" aria-label={t("composer.remove", { name: file.name })} disabled={disabled} onClick={onRemove}><X size={16} /></button>
  </li>;
}

function encodeFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name.slice(0, 200), data: String(reader.result).split(",")[1] });
    reader.onerror = () => reject(Object.assign(new Error("Unable to read the attachment"), { code: "composer.readError" }));
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

export default function Composer({ session, sessions = [], onSend, draft, limitAt, onSelectProject }) {
  const { t } = useTranslation();
  // Queue (end of the turn) or Now (inside the running turn): the
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
  // A withdrawn message to edit starts the composer again with
  // its text; its files must be picked again.
  const [text, setText] = useState(draft?.text || "");
  const input = useRef(null);
  const fileInput = useRef(null);
  const [files, setFiles] = useState([]);
  const [fileError, setFileError] = useState("");
  const selectFiles = (event) => {
    const selected = [...files, ...Array.from(event.target.files || [])];
    event.target.value = "";
    if (selected.length > 4) { setFileError("composer.tooMany"); return; }
    if (selected.some((f) => !ALLOWED_TYPES.includes(f.type) && !((!f.type || f.type === "application/octet-stream") && /\.(pdf|png|jpe?g|webp)$/i.test(f.name)))) {
      setFileError("composer.unsupported"); return;
    }
    if (selected.some((f) => f.size > MAX_FILE_BYTES) || selected.reduce((n, f) => n + f.size, 0) > MAX_TOTAL_BYTES) {
      setFileError("composer.sizeLimit"); return;
    }
    setFiles(selected); setFileError("");
  };
  const [mentions, setMentions] = useState(draft?.mentions || []);
  const [refiles, setRefiles] = useState(draft?.files || []);
  const [query, setQuery] = useState(null);
  const [highlight, setHighlight] = useState(0);

  const [nodeProjects, setNodeProjects] = useState([]);
  useEffect(() => {
    if (!session?.node_id) return;
    let ignore = false;
    fetch(`/api/projects?node_id=${session.node_id}`)
      .then((res) => (res.ok ? res.json() : []))
      .then((data) => {
        if (!ignore) setNodeProjects(data || []);
      })
      .catch(() => {
        if (!ignore) setNodeProjects([]);
      });
    return () => {
      ignore = true;
    };
  }, [session?.node_id]);

  const updateQuery = (value, caret) => {
    const atMatch = value.slice(0, caret).match(/(?:^|\s)@([^@\n]*)$/);
    if (atMatch) {
      setQuery({ type: "session", start: caret - atMatch[1].length - 1, end: caret, text: atMatch[1] });
      setHighlight(0);
      return;
    }
    const hashMatch = value.slice(0, caret).match(/(?:^|\s)#([^#\s\n]*)$/);
    if (hashMatch) {
      setQuery({ type: "project", start: caret - hashMatch[1].length - 1, end: caret, text: hashMatch[1] });
      setHighlight(0);
      return;
    }
    setQuery(null);
  };
  const labels = sessionLabels(sessions);
  const candidates = sessions.filter((s) => s.id !== session?.id && !s.ended_at && !s.excluded)
    .filter((s) => !query?.text || `${providerOf(s).label} ${s.agent_id} ${s.cwd || ""} ${labels[s.id] || sessionName(s)} ${s.id}`
      .toLocaleLowerCase(formattingLocale()).includes(query.text.trim().toLocaleLowerCase(formattingLocale())));

  const effectiveProjects = session?.node_id ? nodeProjects : [];
  const projectCandidates = effectiveProjects.filter((p) =>
    !query?.text || p.name.toLocaleLowerCase(formattingLocale()).includes(query.text.trim().toLocaleLowerCase(formattingLocale()))
  );

  const activeMentions = mentions.filter((m) => text.includes(m.token));
  const choose = (target) => {
    const token = `@${providerOf(target).label} · ${sessionProject(target) || t("composer.noProjectToken")} · ${sessionName(target)} [${target.id.slice(0, 8)}]`;
    const next = text.slice(0, query.start) + token + " " + text.slice(query.end);
    const caret = query.start + token.length + 1;
    setText(next);
    setMentions((prev) => [...prev.filter((m) => m.session_id !== target.id), { session_id: target.id, token }]);
    setQuery(null);
    requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(caret, caret); });
  };

  const chooseProject = (target) => {
    const next = text.slice(0, query.start) + text.slice(query.end);
    const caret = query.start;
    setText(next);
    setQuery(null);
    onSelectProject?.(target.id);
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
      setFileError(error?.code === "composer.readError" ? error.code : "composer.transferError");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="composer" data-testid="composer">
      {closed && <p className="composer-note">{t("composer.closed")}</p>}
      {limitAt && !closed && (
        <p className="composer-note" data-testid="composer-limited">
          {t("composer.limited", { provider: providerOf(session).label, time: resetClock(limitAt) })}
        </p>
      )}
      {suspended && (
        <p className="composer-note" data-testid="composer-suspended">
          {session.provider === "claude-code"
            ? t("composer.suspendedClaude")
            : t("composer.suspendedCodex")}
        </p>
      )}
      {refiles.length > 0 && (
        <p className="composer-note" data-testid="composer-refiles">
          {t("composer.refiles", { names: refiles.join(", ") })}
        </p>
      )}
      {unreachable && (
        <p className="composer-note" data-testid="composer-unreachable">
          {session.provider === "claude-code" && session.entrypoint === "claude-desktop"
            ? t("composer.unreachableClaude")
            : t("composer.unreachable")}
        </p>
      )}
      {busy && (
        <div className="send-mode" role="radiogroup" aria-label={t("composer.when")} data-testid="send-mode">
          <span className="composer-note">{t("composer.working")}</span>
          <button type="button" role="radio" aria-checked={mode !== "now" || !canSteer}
            className={mode !== "now" || !canSteer ? "active" : ""} data-testid="mode-queue"
            onClick={() => setMode("queue")}>
            {t("composer.queue")}
          </button>
          <button type="button" role="radio" aria-checked={mode === "now" && canSteer} disabled={!canSteer}
            className={mode === "now" && canSteer ? "active" : ""} data-testid="mode-now"
            title={canSteer ? t("composer.nowTitle") : t("composer.nowUnsupported")}
            onClick={() => setMode("now")}>
            {t("composer.now")}
          </button>
          <span className="composer-note send-mode-hint">
            {mode === "now" && canSteer
              ? t("composer.nowHint")
              : canSteer ? t("composer.queueHint") : t("composer.queueCodexHint")}
          </span>
        </div>
      )}
      {activeMentions.length > 0 && (
        <p className="composer-note" role="status">{t("composer.references", { count: activeMentions.length })}</p>
      )}
      {query && !closed && (
        <div
          className="mention-picker"
          id={query.type === "project" ? "project-mentions" : "session-mentions"}
          role="listbox"
          aria-label={t(query.type === "project" ? "composer.projectMentionLabel" : "composer.mentionLabel")}
        >
          {query.type === "project" ? (
            (!session?.node_id || effectiveProjects.length === 0) ? (
              <p className="composer-note">{t("composer.noProjectsForSession")}</p>
            ) : projectCandidates.length === 0 ? (
              <p className="composer-note">{t("composer.noMatches")}</p>
            ) : (
              projectCandidates.map((target, i) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={i === highlight}
                  id={`project-mention-${i}`}
                  key={target.id}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => chooseProject(target)}
                >
                  <strong>{target.name}</strong>
                </button>
              ))
            )
          ) : (
            <>
              {candidates.length === 0 && <p className="composer-note">{t("composer.noMatches")}</p>}
              {candidates.map((target, i) => (
                <button
                  type="button"
                  role="option"
                  aria-selected={i === highlight}
                  id={`mention-${i}`}
                  key={target.id}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => choose(target)}
                >
                  <strong>{providerOf(target).label} · {labels[target.id] || sessionName(target)}</strong>
                  <span>{target.cwd || t("composer.noProject")} · {sessionStatus(target).label} · {target.id.slice(0, 8)}</span>
                </button>
              ))}
            </>
          )}
        </div>
      )}
      {fileError && <p className="composer-note" role="alert">{t(fileError)}</p>}
      {files.length > 0 && <>
        <ul className="composer-files" aria-label={t("composer.attachments")}>
          {files.map((file, i) => <FilePreview key={`${file.name}-${i}`} file={file} disabled={sending}
            onRemove={() => { setFiles((prev) => prev.filter((_, n) => n !== i)); setFileError(""); }} />)}
        </ul>
        <p className="composer-note" role="status">{sending ? t("composer.transferring") : t("composer.attachmentHint")}</p>
      </>}
      <input ref={fileInput} type="file" hidden multiple accept={ALLOWED_TYPES.join(",")}
        aria-label={t("composer.chooseFiles")} disabled={closed || sending} onChange={selectFiles} />
      <div className="composer-row">
        <button type="button" className="icon-button attach-button" aria-label={t("composer.attach")}
          disabled={closed || sending} onClick={() => fileInput.current?.click()}><Paperclip size={18} /></button>
        <textarea autoFocus={!!draft}
          ref={input}
          aria-label={t("composer.message")}
          aria-controls={query ? (query.type === "project" ? "project-mentions" : "session-mentions") : undefined}
          aria-expanded={!!query}
          aria-activedescendant={
            query && (query.type === "project" ? projectCandidates[highlight] : candidates[highlight])
              ? (query.type === "project" ? `project-mention-${highlight}` : `mention-${highlight}`)
              : undefined
          }
          data-testid="composer-input"
          placeholder={t(closed ? "composer.closedPlaceholder" : "composer.placeholder")}
          value={text}
          rows={1}
          disabled={closed || sending}
          onChange={(e) => { setText(e.target.value); updateQuery(e.target.value, e.target.selectionStart); }}
          onClick={(e) => updateQuery(text, e.target.selectionStart)}
          onKeyDown={(e) => {
            if (query) {
              const currentList = query.type === "project" ? projectCandidates : candidates;
              if (e.key === "Escape") { e.preventDefault(); setQuery(null); return; }
              if (["ArrowDown", "ArrowUp"].includes(e.key)) {
                e.preventDefault();
                setHighlight((i) => Math.max(0, Math.min(currentList.length - 1, i + (e.key === "ArrowDown" ? 1 : -1))));
                return;
              }
              if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && currentList[highlight]) {
                e.preventDefault();
                if (query.type === "project") {
                  chooseProject(currentList[highlight]);
                } else {
                  choose(currentList[highlight]);
                }
                return;
              }
            }
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
          }}
        />
        <button
          type="button"
          className="icon-button send-button"
          aria-label={t("composer.send")}
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
