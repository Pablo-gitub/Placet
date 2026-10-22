import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import i18n from "../i18n";
import Settings from "../Settings";

function serve({ guard = { enabled: false, five_hour: 90, seven_day: 95, guarded: [] }, renew = false, notifications = { needs_you: true, problems: true, turn_end: "placet" }, putError = false } = {}) {
  const calls = [];
  global.fetch = vi.fn((url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body && JSON.parse(opts.body) });
    let data = {};
    if (url === "/api/settings/usage-guard") data = opts.method === "PUT" ? { ...JSON.parse(opts.body), guarded: guard.guarded } : guard;
    else if (url === "/api/settings/claude-renew") data = { enabled: opts.method === "PUT" ? JSON.parse(opts.body).enabled : renew };
    else if (url === "/api/settings/deliver-at-reset") data = { enabled: opts.method === "PUT" ? JSON.parse(opts.body).enabled : (guard.deliver_at_reset || false) };
    else if (url === "/api/settings/language") data = { language: "it" };
    else if (url === "/api/settings/notifications") {
      if (opts.method === "PUT" && putError) {
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ detail: "error" }) });
      }
      data = opts.method === "PUT" ? JSON.parse(opts.body) : notifications;
    }
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

  it("toggles deliver at reset option", async () => {
    const calls = serve();
    render(<Settings />);
    const toggle = await screen.findByTestId("deliver-at-reset-toggle");
    await waitFor(() => expect(toggle).not.toBeChecked());
    fireEvent.click(toggle);
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url === "/api/settings/deliver-at-reset" && c.body.enabled === true)).toBe(true));
  });
});

describe("notification preferences settings", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders notification preferences loaded from backend", async () => {
    serve({ notifications: { needs_you: true, problems: false, turn_end: "placet" } });
    render(<Settings />);
    const needsYou = await screen.findByTestId("notify-pref-needs-you");
    expect(needsYou).toBeChecked();
    const problems = screen.getByTestId("notify-pref-problems");
    expect(problems).not.toBeChecked();
    expect(screen.getByTestId("notify-turn-end-placet")).toBeChecked();
    expect(screen.getByTestId("notify-turn-end-all")).not.toBeChecked();
    expect(screen.getByTestId("notify-turn-end-off")).not.toBeChecked();
  });

  it("toggles needs_you and problems optimistically and sends PUT", async () => {
    const calls = serve({ notifications: { needs_you: true, problems: true, turn_end: "placet" } });
    render(<Settings />);
    const needsYou = await screen.findByTestId("notify-pref-needs-you");
    fireEvent.click(needsYou);
    expect(needsYou).not.toBeChecked();
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url === "/api/settings/notifications")).toBe(true));
    let put = calls.find((c) => c.method === "PUT" && c.url === "/api/settings/notifications");
    expect(put.body).toEqual({ needs_you: false, problems: true, turn_end: "placet" });

    const problems = screen.getByTestId("notify-pref-problems");
    fireEvent.click(problems);
    expect(problems).not.toBeChecked();
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT" && c.url === "/api/settings/notifications").length).toBe(2));
    const put2 = calls.filter((c) => c.method === "PUT" && c.url === "/api/settings/notifications")[1];
    expect(put2.body).toEqual({ needs_you: false, problems: false, turn_end: "placet" });
  });

  it("changes turn_end mode to all and off", async () => {
    const calls = serve({ notifications: { needs_you: true, problems: true, turn_end: "placet" } });
    render(<Settings />);
    const allRadio = await screen.findByTestId("notify-turn-end-all");
    fireEvent.click(allRadio);
    expect(allRadio).toBeChecked();
    expect(screen.getByTestId("notify-turn-end-placet")).not.toBeChecked();
    await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url === "/api/settings/notifications")).toBe(true));
    const put = calls.find((c) => c.method === "PUT" && c.url === "/api/settings/notifications");
    expect(put.body).toEqual({ needs_you: true, problems: true, turn_end: "all" });
  });

  it("rolls back state and displays error if PUT fails", async () => {
    serve({ notifications: { needs_you: true, problems: true, turn_end: "placet" }, putError: true });
    render(<Settings />);
    const needsYou = await screen.findByTestId("notify-pref-needs-you");
    expect(needsYou).toBeChecked();
    fireEvent.click(needsYou);
    // Optimistic toggle
    expect(needsYou).not.toBeChecked();
    // After failed PUT, rolls back and shows error
    await waitFor(() => expect(needsYou).toBeChecked());
    expect(await screen.findByTestId("notify-prefs-error")).toBeInTheDocument();
  });
});
