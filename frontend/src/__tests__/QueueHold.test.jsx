import { describe, it, expect } from "vitest";
import { holdsQueueUnseen, REPLY_TURN_GRACE_MS } from "../aspects";

const base = {
  id: 230, recipient_session_id: "cx", queue_released_at: null, status: "COMPLETED",
  execution: "started", start_unconfirmed_at: null, response: { content: "fatto", implicit: false },
};

describe("a request holding its session's queue", () => {
  it("is shown once a reply came but the turn end was never seen", () => {
    const now = Date.parse("2026-10-21T13:30:00Z");
    const old = { ...base, completed_at: "2026-10-21T11:21:16Z" };
    expect(holdsQueueUnseen(old, now)).toBe(true);
  });

  it("is not shown right after the reply, while the turn is ending", () => {
    const completed = Date.parse("2026-10-21T11:21:16Z");
    const recent = { ...base, completed_at: "2026-10-21T11:21:16Z" };
    expect(holdsQueueUnseen(recent, completed + REPLY_TURN_GRACE_MS - 1000)).toBe(false);
  });

  it("is not shown once the turn end was seen or the user released it", () => {
    const now = Date.parse("2026-10-21T13:30:00Z");
    expect(holdsQueueUnseen({ ...base, completed_at: "2026-10-21T11:21:16Z", execution: "completed" }, now)).toBe(false);
    expect(holdsQueueUnseen({ ...base, completed_at: "2026-10-21T11:21:16Z", queue_released_at: "2026-10-21T12:00:00Z" }, now)).toBe(false);
  });

  it("keeps the earlier cases", () => {
    expect(holdsQueueUnseen({ ...base, status: "STALLED", execution: "started" })).toBe(true);
    expect(holdsQueueUnseen({ ...base, status: "EXECUTING", start_unconfirmed_at: "2026-10-21T11:20:00Z", execution: "unobserved" })).toBe(true);
    expect(holdsQueueUnseen({ ...base, status: "EXECUTING", execution: "started" })).toBe(false);
  });
});
