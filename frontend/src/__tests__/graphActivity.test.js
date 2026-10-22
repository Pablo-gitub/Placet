import { describe, it, expect } from "vitest";
import { edgeActivities, requestActivity } from "../graphActivity";

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
