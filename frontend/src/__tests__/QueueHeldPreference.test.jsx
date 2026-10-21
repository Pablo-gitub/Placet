import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import i18n from "../i18n";
import NotificationPreferences from "../NotificationPreferences";

function serve(notifications) {
  const calls = [];
  global.fetch = vi.fn((url, opts = {}) => {
    calls.push({ method: opts.method || "GET", body: opts.body && JSON.parse(opts.body) });
    const data = opts.method === "PUT" ? JSON.parse(opts.body) : notifications;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) });
  });
  return calls;
}

describe("held queue notification preference", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the toggle on and saves it when switched off", async () => {
    const calls = serve({ needs_you: true, problems: true, turn_end: "placet", queue_held: true });
    render(<NotificationPreferences />);
    const toggle = await screen.findByTestId("notify-pref-queue-held");
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT").body).toEqual({
      needs_you: true, problems: true, turn_end: "placet", queue_held: false,
    });
  });

  it("has a label in both languages", () => {
    for (const lng of ["en", "it"]) {
      expect(i18n.getFixedT(lng)("settings.notifyQueueHeldTitle")).not.toBe("settings.notifyQueueHeldTitle");
      expect(i18n.getFixedT(lng)("settings.notifyQueueHeldHint")).not.toBe("settings.notifyQueueHeldHint");
    }
  });
});
