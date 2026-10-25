import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../i18n";
import Settings from "../Settings";

function serve({ enabled = false, putFails = false } = {}) {
  const state = { enabled };
  const calls = [];
  global.fetch = vi.fn((url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body && JSON.parse(opts.body) });
    if (url === "/api/settings/session-credentials") {
      if (opts.method === "PUT") {
        if (putFails) return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
        state.enabled = JSON.parse(opts.body).enabled;
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ enabled: state.enabled }) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(Array.isArray(url) ? [] : {}) });
  });
  return calls;
}

describe("verified session credential setting (RS-09)", () => {
  beforeEach(async () => { await i18n.changeLanguage("it"); });
  afterEach(() => vi.restoreAllMocks());

  it("is a switch, off by default, with what it is for and what it covers", async () => {
    serve();
    render(<Settings />);
    const toggle = await screen.findByRole("switch", { name: "Chiave di sessione verificata (sperimentale)" });
    await waitFor(() => expect(toggle).not.toBeChecked());
    const hint = screen.getByTestId("session-credentials-hint");
    expect(hint).toHaveTextContent(/vista coordinatore/i);
    expect(hint).toHaveTextContent(/Claude Code/);
    expect(hint).toHaveTextContent(/nuove sessioni/i);
    expect(hint).toHaveTextContent(/\/compact/);
  });

  it("turns on and off through the API", async () => {
    const calls = serve();
    render(<Settings />);
    const toggle = await screen.findByRole("switch", { name: /Chiave di sessione verificata/ });
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).toBeChecked());
    expect(calls.filter((c) => c.method === "PUT")[0]).toMatchObject({ url: "/api/settings/session-credentials", body: { enabled: true } });
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(calls.filter((c) => c.method === "PUT")[1].body).toEqual({ enabled: false });
  });

  it("shows the saved state when it was already on", async () => {
    serve({ enabled: true });
    render(<Settings />);
    expect(await screen.findByRole("switch", { name: /Chiave di sessione verificata/ })).toBeChecked();
  });

  it("goes back when the change is refused", async () => {
    serve({ putFails: true });
    render(<Settings />);
    const toggle = await screen.findByRole("switch", { name: /Chiave di sessione verificata/ });
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
  });

  it("is in English too", async () => {
    await i18n.changeLanguage("en");
    serve();
    render(<Settings />);
    expect(await screen.findByRole("switch", { name: "Verified session key (experimental)" })).toBeInTheDocument();
    expect(screen.getByTestId("session-credentials-hint")).toHaveTextContent(/coordinator view/i);
    await i18n.changeLanguage("it");
  });
});
