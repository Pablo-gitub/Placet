import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

// How close to the bottom (px) the reader counts as following the conversation, and how close to the top an older
// page is asked for.
export const FOLLOW_PX = 80;
export const LOAD_OLDER_PX = 150;
// A scroll event within this long of the code's own scroll, at the position the code asked for, is the code's.
const OWN_SCROLL_MS = 400;

/**
 * The one owner of the conversation's scroll.
 *
 * - While the reader is at the bottom (within FOLLOW_PX) the box stays at the bottom, whatever grows.
 * - Otherwise it stays on the first message the reader sees, at the same distance from the top edge: an anchor kept up to
 *   date while they read and restored after every change of content (older messages prepended, an image or a font that
 *   loads above, the keyboard, the composer). The browser's own anchoring is off (overflow-anchor: none).
 * - A scroll the code made itself never decides whether the reader is following; only the reader's does.
 * - New content below a reader who is higher up is announced (`unseen`), never pulled to.
 *
 * `signature` says what the content is: { first, last, tail } (the first and last entry, and how many live items follow).
 */
export function useChatScroll({ signature, hasOlder, loadingOlder, loadOlder }) {
  const scrollRef = useRef(null);
  const contentRef = useRef(null);
  const following = useRef(true);
  const anchor = useRef(null);            // { node, offset } the first message the reader sees
  const lastTop = useRef(0);
  const own = useRef({ top: null, until: 0 });
  const measured = useRef({ height: 0, top: 0 });
  const seen = useRef({ first: undefined, last: undefined, tail: 0 });
  const [unseen, setUnseen] = useState(false);

  const setTop = useCallback((top) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = top;
    lastTop.current = el.scrollTop;
    own.current = { top: el.scrollTop, until: Date.now() + OWN_SCROLL_MS };
  }, []);

  // The first message the reader sees and how far it is from the top edge.
  const takeAnchor = useCallback(() => {
    const el = scrollRef.current;
    const content = contentRef.current;
    if (!el || !content) return;
    const box = el.getBoundingClientRect();
    anchor.current = null;
    for (const node of content.children) {
      const rect = node.getBoundingClientRect();
      if (rect.height > 0 && rect.bottom > box.top + 1) {
        anchor.current = { node, offset: rect.top - box.top };
        break;
      }
    }
    measured.current = { height: el.scrollHeight, top: el.scrollTop };
  }, []);

  // Brings the box back to where the reader was: the bottom if they follow, else the same message at the same distance.
  const settle = useCallback((prepended) => {
    const el = scrollRef.current;
    if (!el) return;
    if (following.current) {
      setTop(el.scrollHeight);
    } else if (anchor.current?.node.isConnected && anchor.current.node.getBoundingClientRect().height > 0) {
      const box = el.getBoundingClientRect();
      const delta = anchor.current.node.getBoundingClientRect().top - box.top - anchor.current.offset;
      if (Math.abs(delta) > 0.5) setTop(el.scrollTop + delta);
    } else if (prepended) {
      // Nothing to measure (no layout yet): what came in above is the growth of the content since it was last measured.
      setTop(measured.current.top + (el.scrollHeight - measured.current.height));
    }
    if (!following.current) takeAnchor();
    else measured.current = { height: el.scrollHeight, top: el.scrollTop };
  }, [setTop, takeAnchor]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const previous = seen.current;
    const appended = signature.last !== previous.last || signature.tail > previous.tail;
    const prepended = signature.first !== previous.first && previous.first !== undefined && signature.last === previous.last;
    seen.current = signature;
    settle(prepended);
    if (appended && !following.current) {
      // oxlint-disable-next-line react/set-state-in-effect
      setUnseen(true);
    }
  }, [signature.first, signature.last, signature.tail]); // eslint-disable-line react-hooks/exhaustive-deps

  // Whatever changes size without changing the entries (an image, a font, the keyboard, the composer).
  useEffect(() => {
    const el = scrollRef.current;
    const content = contentRef.current;
    if (!el || !content || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() => settle(false));
    observer.observe(el);
    observer.observe(content);
    return () => observer.disconnect();
  }, [settle]);

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const mine = own.current;
    const code = mine.top !== null && Date.now() <= mine.until && Math.abs(el.scrollTop - mine.top) <= 2;
    lastTop.current = el.scrollTop;
    if (code) return;                                   // the code's own scroll never decides anything
    following.current = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_PX;
    if (following.current) setUnseen((was) => (was ? false : was));
    else takeAnchor();
    if (el.scrollTop < LOAD_OLDER_PX && hasOlder && !loadingOlder) loadOlder();
  }, [hasOlder, loadingOlder, loadOlder, takeAnchor]);

  const scrollToEnd = useCallback(() => {
    following.current = true;
    const el = scrollRef.current;
    if (el) setTop(el.scrollHeight);
    setUnseen(false);
  }, [setTop]);

  // The reader sent something: it goes to the bottom with them.
  const follow = useCallback(() => {
    following.current = true;
  }, []);

  return { scrollRef, contentRef, unseen, onScroll, scrollToEnd, follow };
}
