import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import i18n from "../i18n";
import Settings from "../Settings";

function serve({ guard = { enabled: false, five_hour: 90, seven_day: 95, guarded: [] }, renew = false } = {}) {
  const calls = [];
  global.fetch = vi.fn((url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body && JSON.parse(opts.body) });
    let data = {};
    if (url === "/api/settings/usage-guard") data = opts.method === "PUT" ? { ...JSON.parse(opts.body), guarded: guard.guarded } : guard;
    else if (url === "/api/settings/claude-renew") data = { enabled: opts.method === "PUT" ? JSON.parse(opts.body).enabled : renew };
    else if (url === "/api/settings/language") data = { language: "it" };
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) });
  });
  return calls;
}

describe("usage guard settings", () => {
  afterEach(() => vi.restoreAllMocks());

  it("turns the guard on with custom thresholds", async () => {
    const calls = serve();
    render(<Settings />);
    const toggle = await screen.findByTestId("usage-guard-toggle");
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(screen.getByTestId("usage-guard-5h")).toHaveValue(90);
    expect(screen.getByTestId("usage-guard-7d")).toHaveValue(95);
    fireEvent.click(toggle);
    fireEvent.change(screen.getByTestId("usage-guard-5h"), { target: { value: "85" } });
    fireEvent.click(screen.getByTestId("usage-guard-save"));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url === "/api/settings/usage-guard")).toBe(true));
    const put = calls.find((c) => c.method === "PUT" && c.url === "/api/settings/usage-guard");
    expect(put.body).toEqual({ enabled: true, five_hour: 85, seven_day: 95 });
  });

  it("shows which providers are reviewed now", async () => {
    serve({ guard: { enabled: true, five_hour: 90, seven_day: 95, guarded: ["codex"] }, renew: true });
    render(<Settings />);
    expect(await screen.findByTestId("usage-guard-active")).toHaveTextContent("Codex");
  });

  it("warns that Claude needs automatic renewal, and offers it right there", async () => {
    const calls = serve({ guard: { enabled: true, five_hour: 90, seven_day: 95, guarded: [] }, renew: false });
    render(<Settings />);
    expect(await screen.findByTestId("usage-guard-claude-warning")).toHaveTextContent(i18n.t("settings.guardClaudeWarning"));
    const renew = screen.getByTestId("toggle-claude-auto-renew");
    fireEvent.click(renew);
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url === "/api/settings/claude-renew" && c.body.enabled === true)).toBe(true));
    await waitFor(() => expect(screen.queryByTestId("usage-guard-claude-warning")).toBeNull());
  });
});
