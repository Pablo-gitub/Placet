import { describe, it, expect } from "vitest";
import { edgeActivities, edgeSchedules, requestActivity } from "../graphActivity";

const req = (over) => ({ id: 1, relation_id: 7, status: "APPROVED", ...over });

describe("what a request is doing on the graph", () => {
  it("moves only while a delivery is really in progress", () => {
    expect(requestActivity(req({ status: "DELIVERING" }))).toBe("delivering");
  });

  it("is merely queued while approved and not yet being delivered", () => {
    expect(requestActivity(req({ status: "APPROVED" }))).toBe("queued");
  });

  it("keeps the other states as they were", () => {
    expect(requestActivity(req({ status: "PENDING" }))).toBe("waiting");
    expect(requestActivity(req({ status: "EXECUTING" }))).toBe("executing");
    expect(requestActivity(req({ status: "COMPLETED", response: { content: "ok" }, allow_auto_return: true }))).toBe("returning");
    expect(requestActivity(req({ status: "COMPLETED", response: { content: "ok" }, allow_auto_return: false }))).toBe("waiting");
    expect(requestActivity(req({ status: "COMPLETED", delivered_at: "2026-10-19T00:00:00Z" }))).toBeNull();
  });

  it("ranks waiting > delivering > returning > queued > executing on a shared relation", () => {
    const of = (...statuses) => edgeActivities(statuses.map((status, i) => req({
      id: i, status, response: status === "COMPLETED" ? { content: "ok" } : null, allow_auto_return: true,
    })))[7];
    expect(of("APPROVED", "DELIVERING")).toBe("delivering");
    expect(of("APPROVED", "PENDING")).toBe("waiting");
    expect(of("APPROVED", "COMPLETED")).toBe("returning");
    expect(of("EXECUTING", "APPROVED")).toBe("queued");
  });
});

describe("a request scheduled for later", () => {
  const later = new Date(Date.now() + 3 * 3600000).toISOString();
  const earlier = new Date(Date.now() - 60000).toISOString();

  it("is told apart from a plain queued one while its time is ahead", () => {
    expect(requestActivity(req({ status: "APPROVED", scheduled_at: later }))).toBe("scheduled");
    expect(requestActivity(req({ status: "APPROVED", scheduled_at: earlier }))).toBe("queued");
    expect(requestActivity(req({ status: "APPROVED", scheduled_at: null }))).toBe("queued");
  });

  it("never hides a plain queued or live request on the same relation", () => {
    const of = (...rows) => edgeActivities(rows.map((r, i) => req({ id: i, ...r })))[7];
    expect(of({ status: "APPROVED", scheduled_at: later }, { status: "APPROVED" })).toBe("queued");
    expect(of({ status: "APPROVED", scheduled_at: later }, { status: "EXECUTING" })).toBe("executing");
    expect(of({ status: "APPROVED", scheduled_at: later })).toBe("scheduled");
  });

  it("gives the earliest delivery time of the scheduled ones", () => {
    const soon = new Date(Date.now() + 3600000).toISOString();
    expect(edgeSchedules([
      req({ id: 1, status: "APPROVED", scheduled_at: later }),
      req({ id: 2, status: "APPROVED", scheduled_at: soon }),
      req({ id: 3, status: "APPROVED", relation_id: 8, scheduled_at: null }),
    ])).toEqual({ 7: soon });
  });
});
