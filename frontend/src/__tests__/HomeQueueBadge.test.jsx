import React from "react";
import { render, screen } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import Home from "../Home";
import useHoldClock from "../useHoldClock";
import { queueStuck, REPLY_TURN_GRACE_MS } from "../aspects";

const held = (overrides = {}) => ({
  id: 10, recipient_session_id: "cx1", queue_released_at: null, status: "STALLED", execution: "started",
  start_unconfirmed_at: null, question_type: "message", ...overrides,
});
const waiting = (overrides = {}) => ({
  id: 11, recipient_session_id: "cx1", status: "APPROVED", execution: "unobserved", question_type: "message",
  ...overrides,
});
const session = (id) => ({
  id, provider: "codex", agent_id: "codex", pinned: true, title: id, state: "idle", cwd: "/p",
  activity_at: "2026-10-21T10:00:00Z",
});

describe("a session whose queue is held with messages waiting", () => {
  it("needs both a held request and an approved message behind it", () => {
    expect(queueStuck([held(), waiting()], "cx1")).toBe(true);
    expect(queueStuck([held()], "cx1")).toBe(false);
    expect(queueStuck([waiting()], "cx1")).toBe(false);
    expect(queueStuck([held({ queue_released_at: "2026-10-21T11:00:00Z" }), waiting()], "cx1")).toBe(false);
    expect(queueStuck([held(), waiting({ recipient_session_id: "other" })], "cx1")).toBe(false);
  });

  it("waits out the reply grace like the hold card does", () => {
    const completed = Date.parse("2026-10-21T11:21:16Z");
    const answered = held({ status: "COMPLETED", completed_at: "2026-10-21T11:21:16Z" });
    expect(queueStuck([answered, waiting()], "cx1", completed + 1000)).toBe(false);
    expect(queueStuck([answered, waiting()], "cx1", completed + REPLY_TURN_GRACE_MS)).toBe(true);
  });
});

describe("Home session list", () => {
  const props = { sessions: [session("cx1"), session("cx2")], usage: null, onDecide() {}, onOpenSession() {}, onTogglePin() {} };

  it("marks only the stuck session with a badge", () => {
    render(<Home {...props} requests={[held(), waiting()]} />);
    expect(screen.getByTestId("session-held-cx1")).toBeInTheDocument();
    expect(screen.queryByTestId("session-held-cx2")).toBeNull();
  });

  it("has no badge when nothing waits behind the hold", () => {
    render(<Home {...props} requests={[held()]} />);
    expect(screen.queryByTestId("session-held-cx1")).toBeNull();
  });
});

describe("the hold clock without a session", () => {
  afterEach(() => vi.useRealTimers());

  it("arms the grace deadline of any session", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const answered = held({ status: "COMPLETED", completed_at: new Date(Date.now() - 30_000).toISOString() });
    renderHook(() => useHoldClock([answered], null));
    expect(vi.getTimerCount()).toBe(1);
  });
});
