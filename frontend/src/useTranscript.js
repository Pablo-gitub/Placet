import { apiErrorDescriptor } from "./i18n/errors";
import { useCallback, useEffect, useRef, useState } from "react";
import { mergeEntries } from "./chatEntries";

const PAGE_SIZE = 40;

async function fetchPage(sessionId, params) {
  const query = new URLSearchParams({ limit: String(PAGE_SIZE), ...params });
  const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/transcript?${query}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw Object.assign(new Error("Transcript request failed"), { display: apiErrorDescriptor(body, res.status) });
  }
  return res.json();
}

/**
 * The conversation of a session, page by page (RF-30): the newest entries
 * first, older ones with loadOlder(), new ones whenever `refreshKey`
 * changes (the session started or finished a turn).
 */
export function useTranscript(sessionId, refreshKey) {
  const [entries, setEntries] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [error, setError] = useState(null);
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const cursors = useRef({ before: null, end: null });
  const busy = useRef(false);
  const [attempt, setAttempt] = useState(0);
  // RF-61: model, effort and context, from the newest page.
  const [meta, setMeta] = useState(null);

  useEffect(() => {
    let cancelled = false;
    cursors.current = { before: null, end: null };
    // Data fetching: state changes when the page arrives.
    // oxlint-disable-next-line react/set-state-in-effect
    setStatus("loading");
    setEntries([]);
    fetchPage(sessionId, {})
      .then((page) => {
        if (cancelled) return;
        cursors.current = { before: page.before, end: page.end };
        setEntries(page.messages);
        setMeta(page.meta || null);
        setHasOlder(page.before !== null && page.before !== undefined);
        setError(null);
        setStatus("ready");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e.display || { key: "errors.transcriptConnection" });
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, attempt]);

  // New entries after a turn started or ended.
  useEffect(() => {
    if (refreshKey === undefined || cursors.current.end === null) return;
    let cancelled = false;
    fetchPage(sessionId, { after: String(cursors.current.end) })
      .then((page) => {
        if (cancelled) return;
        cursors.current.end = page.end;
        if (page.meta) setMeta(page.meta);
        if (page.messages.length) setEntries((prev) => mergeEntries(prev, page.messages));
      })
      .catch(() => {}); // the next turn retries; the page itself stays usable
    return () => {
      cancelled = true;
    };
  }, [sessionId, refreshKey]);

  const loadOlder = useCallback(async () => {
    const before = cursors.current.before;
    if (busy.current || before === null || before === undefined) return false;
    busy.current = true;
    setLoadingOlder(true);
    try {
      const page = await fetchPage(sessionId, { before: String(before) });
      cursors.current.before = page.before;
      setEntries((prev) => mergeEntries(page.messages, prev));
      setError(null);
      setHasOlder(page.before !== null && page.before !== undefined);
      return true;
    } catch (e) {
      setError(e.display || { key: "errors.transcriptConnection" });
      return false;
    } finally {
      busy.current = false;
      setLoadingOlder(false);
    }
  }, [sessionId]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  return { entries, status, error, hasOlder, loadingOlder, loadOlder, retry, meta };
}
