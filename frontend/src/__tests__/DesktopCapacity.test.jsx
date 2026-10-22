import React from "react";
import { render, screen, act, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import DesktopCapacity from "../DesktopCapacity";
import Home from "../Home";
import i18n from "../i18n";

const now = () => new Date().toISOString();

function serve(body, ok = true) {
  const calls = [];
  global.fetch = vi.fn((url) => {
    calls.push(url);
    return ok ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) }) : Promise.reject(new Error("down"));
  });
  return calls;
}

describe("Claude Desktop capacity line", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it("shows the loaded sessions against the app's cap, quietly below it", async () => {
    serve({ engines: 6, cap: 7, observed_at: now() });
    render(<DesktopCapacity />);
    const line = await screen.findByTestId("desktop-capacity");
    expect(line).toHaveTextContent(i18n.t("capacity.line", { engines: 6, cap: 7 }));
    expect(i18n.getFixedT("it")("capacity.line", { engines: 6, cap: 7 })).toBe("Claude Desktop: 6/7 sessioni caricate");
    expect(line).not.toHaveClass("capacity-full");
    expect(screen.queryByTestId("desktop-capacity-hint")).toBeNull();
  });

  it("warns, with the hint, at or above the cap", async () => {
    for (const engines of [7, 9]) {
      serve({ engines, cap: 7, observed_at: now() });
      const { unmount } = render(<DesktopCapacity />);
      expect(await screen.findByTestId("desktop-capacity")).toHaveClass("capacity-full");
      expect(screen.getByTestId("desktop-capacity-hint")).toHaveTextContent(i18n.t("capacity.hint"));
      unmount();
    }
    expect(i18n.getFixedT("it")("capacity.hint")).toBe(
      "I risvegli da Placet potrebbero non partire: archivia una conversazione inattiva nell'app");
  });

  it("shows nothing without numbers, with old ones, or when the request fails", async () => {
    for (const [body, ok] of [[{}, true], [{ engines: 6, cap: 7, observed_at: "2020-01-01T00:00:00Z" }, true], [null, false]]) {
      serve(body, ok);
      const { container, unmount } = render(<DesktopCapacity />);
      await waitFor(() => expect(global.fetch).toHaveBeenCalled());
      await act(async () => {});
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });

  it("looks again every minute", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout", "Date"] });
    const calls = serve({ engines: 3, cap: 7, observed_at: now() });
    render(<DesktopCapacity />);
    await act(async () => { await Promise.resolve(); });
    expect(calls).toHaveLength(1);
    await act(async () => { vi.advanceTimersByTime(60_000); await Promise.resolve(); });
    expect(calls).toHaveLength(2);
  });

  it("has both languages", () => {
    for (const lng of ["en", "it"]) {
      for (const key of ["capacity.line", "capacity.hint"]) expect(i18n.getFixedT(lng)(key)).not.toBe(key);
    }
  });
});

describe("in Home", () => {
  afterEach(() => vi.restoreAllMocks());

  const session = (over) => ({
    id: "s1", agent_id: "claude", provider: "claude-code", entrypoint: "claude-desktop", cwd: "/p", title: "A", state: "idle",
    reachable: true, pinned: true, activity_at: "2026-10-22T10:00:00Z", started_at: "2026-10-22T09:00:00Z",
    last_seen_at: "2026-10-22T10:00:00Z", ...over,
  });
  const home = (sessions) => render(<Home requests={[]} sessions={sessions} usage={null} onDecide={vi.fn()}
    onOpenSession={vi.fn()} onTogglePin={vi.fn()} />);

  it("is in the Claude group only", async () => {
    const calls = serve({ engines: 6, cap: 7, observed_at: now() });
    home([session({}), session({ id: "c1", provider: "codex", agent_id: "codex", entrypoint: "cli" })]);
    const line = await screen.findByTestId("desktop-capacity");
    expect(screen.getByTestId("provider-group-claude-code")).toContainElement(line);
    expect(screen.getByTestId("provider-group-codex").querySelector('[data-testid="desktop-capacity"]')).toBeNull();
    expect(calls.filter((u) => u.includes("claude-desktop"))).toHaveLength(1);
  });

  it("is absent when the Claude sessions do not run in the app", async () => {
    const calls = serve({ engines: 6, cap: 7, observed_at: now() });
    home([session({ entrypoint: "cli" })]);
    await act(async () => {});
    expect(screen.queryByTestId("desktop-capacity")).toBeNull();
    expect(calls.filter((u) => u.includes("claude-desktop"))).toHaveLength(0);
  });

  it("is absent, and asks nothing, when there is no Claude session", async () => {
    const calls = serve({ engines: 6, cap: 7, observed_at: now() });
    home([session({ id: "c1", provider: "codex", agent_id: "codex", entrypoint: "cli" })]);
    await act(async () => {});
    expect(screen.queryByTestId("desktop-capacity")).toBeNull();
    expect(calls.filter((u) => u.includes("claude-desktop"))).toHaveLength(0);
  });
});
