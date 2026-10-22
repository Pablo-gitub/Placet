import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import SessionView from "../SessionView";
import WakeNotice from "../WakeNotice";
import i18n from "../i18n";
import { mockFetch, transcriptOf } from "./testUtils";

const session = (over = {}) => ({
  id: "s1", agent_id: "claude", provider: "claude-code", entrypoint: "claude-desktop", cwd: "/p/lab",
  title: "Lab", state: "idle", reachable: false, activity_at: "2026-10-22T10:00:00Z",
  started_at: "2026-10-22T09:00:00Z", last_seen_at: "2026-10-22T10:00:00Z",
  wake_state: "wake_unconfirmed", wake_at: "2026-10-22T10:05:00Z", wake_attempts: 1, ...over,
});

function serve(status = 200, body = { session: "s1" }) {
  const calls = [];
  global.fetch = vi.fn((url, opts = {}) => {
    calls.push({ url, method: opts.method });
    return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
  });
  return calls;
}

describe("wake-up notice", () => {
  afterEach(() => vi.restoreAllMocks());

  it("says what happened, honestly, with the last attempt and a retry button", () => {
    render(<WakeNotice session={session()} />);
    const notice = screen.getByTestId("wake-notice");
    expect(notice).toHaveTextContent(i18n.t("wake.unconfirmed"));
    expect(notice).toHaveTextContent(i18n.t("wake.lastAttempt").split("{{")[0].trim());
    expect(screen.getByTestId("wake-retry")).toBeEnabled();
  });

  it("retries by asking the backend for a new episode", async () => {
    const calls = serve();
    render(<WakeNotice session={session()} />);
    fireEvent.click(screen.getByTestId("wake-retry"));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ url: "/api/sessions/s1/wake", method: "POST" });
  });

  it("shows a translated message when the retry is refused", async () => {
    serve(409, { code: "session.wake_in_progress", params: {}, detail: "raw server text" });
    render(<WakeNotice session={session()} />);
    fireEvent.click(screen.getByTestId("wake-retry"));
    const error = await screen.findByTestId("wake-error");
    expect(error).toHaveTextContent(i18n.t("errors.wakeInProgress"));
    expect(error).not.toHaveTextContent("raw server text");
  });

  it("shows a waiting line, without a button, while the window runs", () => {
    for (const wake_state of ["open_requested", "waiting_for_session"]) {
      const { unmount } = render(<WakeNotice session={session({ wake_state })} />);
      expect(screen.getByTestId("wake-notice")).toHaveTextContent(i18n.t("wake.waiting"));
      expect(screen.queryByTestId("wake-retry")).toBeNull();
      unmount();
    }
  });

  it("tells a link that never reached the app apart", () => {
    render(<WakeNotice session={session({ wake_state: "wake_failed" })} />);
    expect(screen.getByTestId("wake-notice")).toHaveTextContent(i18n.t("wake.failed"));
    expect(screen.getByTestId("wake-retry")).toBeInTheDocument();
  });

  it("shows nothing for a reachable session, other providers or no episode", () => {
    for (const over of [{ wake_state: "reachable" }, { wake_state: null }, { reachable: true }, { entrypoint: "cli" }]) {
      const { container, unmount } = render(<WakeNotice session={session(over)} />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });

  it("appears in the session view, where the message stays editable", async () => {
    mockFetch([[transcriptOf("s1"), { session_id: "s1", provider: "claude-code", before: 0, end: 0, messages: [] }]]);
    render(<SessionView sessionId="s1" session={session()} requests={[]} sessions={[session()]}
      onBack={vi.fn()} onDecide={vi.fn()} onSend={vi.fn()} />);
    expect(screen.getByTestId("wake-notice")).toBeInTheDocument();
  });

  it("names the cause the app's log gave, in careful words, and only then", () => {
    const { rerender } = render(<WakeNotice session={session({ wake_cause: "warm_capacity_denied", wake_cause_at: "2026-10-22T10:06:00Z" })} />);
    expect(screen.getByTestId("wake-cause")).toHaveTextContent(i18n.t("wake.capacityDenied"));
    rerender(<WakeNotice session={session({ wake_state: "waiting_for_session", wake_cause: "warm_capacity_denied" })} />);
    expect(screen.getByTestId("wake-cause")).toBeInTheDocument();
    rerender(<WakeNotice session={session({ wake_cause: null })} />);
    expect(screen.queryByTestId("wake-cause")).toBeNull();
    rerender(<WakeNotice session={session({ wake_cause: "something_unknown" })} />);   // only the closed code is shown
    expect(screen.queryByTestId("wake-cause")).toBeNull();
    rerender(<WakeNotice session={session({ wake_state: "wake_failed", wake_cause: "warm_capacity_denied" })} />);
    expect(screen.queryByTestId("wake-cause")).toBeNull();                               // the link never reached the app
  });

  it("never claims more than the log said", () => {
    for (const lng of ["en", "it"]) {
      const text = i18n.getFixedT(lng)("wake.capacityDenied");
      expect(text).not.toMatch(/RAM|memory|memoria|sette|seven|\b7\b|sospes|suspended|evict|chiuse|closed automatically/i);
    }
    expect(i18n.getFixedT("it")("wake.capacityDenied")).toBe(
      "Claude Desktop ha segnalato il limite delle sessioni precaricate durante questo tentativo. L'avvio non è confermato; il messaggio è ancora in Placet: scrivi un messaggio nella chat sul PC per avviarla.");
  });

  it("has both languages", () => {
    for (const lng of ["en", "it"]) {
      for (const key of ["wake.unconfirmed", "wake.failed", "wake.waiting", "wake.capacityDenied", "wake.lastAttempt", "wake.retry",
        "errors.wakeInProgress", "errors.wakeNothing", "errors.wakeReachable"]) {
        expect(i18n.getFixedT(lng)(key)).not.toBe(key);
      }
    }
    expect(i18n.getFixedT("it")("wake.unconfirmed")).toContain("scrivi un messaggio nella chat sul PC");
  });
});
