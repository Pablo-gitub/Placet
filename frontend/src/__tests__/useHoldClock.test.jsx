import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import useHoldClock from "../useHoldClock";
import { holdsQueueUnseen, REPLY_TURN_GRACE_MS } from "../aspects";

const answered = (overrides = {}) => ({
  id: 230, recipient_session_id: "cx", queue_released_at: null, status: "COMPLETED", execution: "started",
  start_unconfirmed_at: null, completed_at: new Date(Date.now() - 30_000).toISOString(), ...overrides,
});

describe("the queue-hold clock", () => {
  afterEach(() => vi.useRealTimers());

  it("shows the hold once the grace expires, with no other event", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const requests = [answered()];
    const { result } = renderHook(() => useHoldClock(requests, "cx"));
    expect(holdsQueueUnseen(requests[0], result.current)).toBe(false);
    act(() => { vi.advanceTimersByTime(REPLY_TURN_GRACE_MS - 30_000 + 10); });
    expect(holdsQueueUnseen(requests[0], result.current)).toBe(true);
  });

  it("sets no timer when the turn end arrived or the queue was released", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const ended = [answered({ execution: "completed" }), answered({ id: 231, queue_released_at: "2026-10-21T12:00:00Z" })];
    renderHook(() => useHoldClock(ended, "cx"));
    expect(vi.getTimerCount()).toBe(0);
  });

  it("removes its timer on unmount", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const { unmount } = renderHook(() => useHoldClock([answered()], "cx"));
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("the queue-hold clock and page visibility", () => {
  let state = "visible";
  const setVisibility = (value) => {
    state = value;
    document.dispatchEvent(new Event("visibilitychange"));
  };
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  afterEach(() => { vi.useRealTimers(); state = "visible"; });

  it("arms the timer when a page mounted hidden becomes visible before the deadline", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    state = "hidden";
    const requests = [answered()];
    const { result } = renderHook(() => useHoldClock(requests, "cx"));
    expect(vi.getTimerCount()).toBe(0);
    act(() => { vi.advanceTimersByTime(10_000); setVisibility("visible"); });
    expect(vi.getTimerCount()).toBe(1);
    act(() => { vi.advanceTimersByTime(REPLY_TURN_GRACE_MS); });
    expect(holdsQueueUnseen(requests[0], result.current)).toBe(true);
  });

  it("cancels the timer when the page goes to the background", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    renderHook(() => useHoldClock([answered()], "cx"));
    expect(vi.getTimerCount()).toBe(1);
    act(() => setVisibility("hidden"));
    expect(vi.getTimerCount()).toBe(0);
  });

  it("shows the hold at once when the page returns after the deadline", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const requests = [answered()];
    const { result } = renderHook(() => useHoldClock(requests, "cx"));
    act(() => setVisibility("hidden"));
    act(() => { vi.advanceTimersByTime(REPLY_TURN_GRACE_MS); });
    act(() => setVisibility("visible"));
    expect(holdsQueueUnseen(requests[0], result.current)).toBe(true);
  });

  it("follows the session and drops the timer when the hold ends while waiting", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const { rerender } = renderHook(({ reqs, session }) => useHoldClock(reqs, session),
      { initialProps: { reqs: [answered()], session: "cx" } });
    expect(vi.getTimerCount()).toBe(1);
    rerender({ reqs: [answered()], session: "other" });
    expect(vi.getTimerCount()).toBe(0);
    rerender({ reqs: [answered()], session: "cx" });
    expect(vi.getTimerCount()).toBe(1);
    rerender({ reqs: [answered({ queue_released_at: "2026-10-21T12:00:00Z" })], session: "cx" });
    expect(vi.getTimerCount()).toBe(0);
  });
});
