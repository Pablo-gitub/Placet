import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Commands from "../Commands";
import App from "../App";
import { routeFromHash } from "../decisions";
import i18n from "../i18n";
import { json, MockEventSource } from "./testUtils";

const firstId = "a".repeat(32), nextId = "b".repeat(32), jobId = "c".repeat(32);
const action = { id: "restart-backend", enabled: true, parameters: {}, summary: "Restart backend" };
let records, bodies, mode, dropped, configured, blocked;
function view(id = firstId, state = "awaiting_confirmation") {
  return { id, action: "restart-backend", enabled: state !== "expired", state,
    confirmation_url: `https://admin.example.test/?proposal=${id}`, expires_at: Date.now() / 1000 + 900,
    job_id: ["awaiting_confirmation", "expired"].includes(state) ? null : jobId, evidence: "system_service_health" };
}
beforeEach(async () => {
  localStorage.clear();
  await i18n.changeLanguage("en");
  window.location.hash = "#/comandi";
  records = {}; bodies = []; mode = "ok"; dropped = false; configured = true; blocked = null;
  vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
    if (url === "/api/admin/catalogue") {
      if (mode === "recover") return json({}, 503);
      return json(configured ? { status: "available", execution_enabled: true, actions: [action], recent: Object.values(records), blocked_job_id: blocked }
        : { status: "not_configured", execution_enabled: false, actions: [] });
    }
    if (url === "/api/admin/proposals" && options.method === "POST") {
      const body = JSON.parse(options.body); bodies.push(body);
      const result = records[body.request_key] || view(Object.keys(records).length ? nextId : firstId);
      records[body.request_key] = result;
      if (mode === "lost" && !dropped) { dropped = true; return Promise.reject(new TypeError("private network diagnostic")); }
      return json(result, 201);
    }
    if (url.startsWith("/api/admin/proposals/")) return json(Object.values(records).find((v) => v.id === url.split("/").pop()) || {}, 404);
    if (url === "/api/events") return json({});
    if (url.startsWith("/api/settings/")) return json({ enabled: false, five_hour: 90, seven_day: 95, guarded: [], language: "en" });
    return json([]);
  }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear(); });

it("prepares an intent and opens confirmation only through a separate explicit link", async () => {
  const open = vi.spyOn(window, "open");
  render(<Commands />);
  fireEvent.click(await screen.findByRole("button", { name: "Run" }));
  const link = await screen.findByRole("link", { name: "Confirm with a passkey" });
  expect(link).toHaveAttribute("href", `https://admin.example.test/?proposal=${firstId}`);
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.getByText("admin.example.test")).toBeInTheDocument();
  expect(open).not.toHaveBeenCalled();
  expect(bodies).toHaveLength(1);
  expect(bodies[0]).toEqual({ action: "restart-backend", parameters: {}, request_key: expect.any(String) });
});

it("keeps a lost-ack key across remount and retries the same intent only on a tap", async () => {
  mode = "lost";
  const first = render(<Commands />);
  fireEvent.click(await screen.findByRole("button", { name: "Run" }));
  await screen.findByRole("button", { name: "Retry preparation" });
  const key = bodies[0].request_key;
  first.unmount();
  render(<Commands />);
  const retry = await screen.findByRole("button", { name: "Retry preparation" });
  expect(bodies).toHaveLength(1);
  fireEvent.click(retry);
  await screen.findByRole("link", { name: "Confirm with a passkey" });
  expect(bodies).toHaveLength(2);
  expect(bodies[1].request_key).toBe(key);
});

it("uses a new key for a new intention after expiry", async () => {
  render(<Commands />);
  fireEvent.click(await screen.findByRole("button", { name: "Run" }));
  await screen.findByRole("link", { name: "Confirm with a passkey" });
  records[bodies[0].request_key].state = "expired";
  records[bodies[0].request_key].enabled = false;
  // Returning from the confirmation page requests a fresh saved state.
  fireEvent(window, new Event("focus"));
  fireEvent.click(await screen.findByRole("button", { name: "New proposal" }));
  await screen.findByRole("link", { name: "Confirm with a passkey" });
  expect(bodies).toHaveLength(2);
  expect(bodies[1].request_key).not.toBe(bodies[0].request_key);
});

it("does not create an intent if its retry reference cannot be saved", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
  render(<Commands />);
  fireEvent.click(await screen.findByRole("button", { name: "Run" }));
  await screen.findByText(i18n.t("commands.storageUnavailable"));
  expect(bodies).toHaveLength(0);
});

it("has no executable control when not configured", async () => {
  configured = false;
  render(<Commands />);
  await screen.findByText(i18n.t("commands.notConfigured"));
  expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  expect(screen.getByRole("link", { name: "Installation guide" })).toHaveAttribute("href", expect.stringContaining("docs/installation.md"));
  expect(bodies).toHaveLength(0);
});

it("shows the reconciliation gate even when its job is absent from recent proposals", async () => {
  blocked = jobId;
  render(<Commands />);
  await screen.findByText(i18n.t("commands.blocked"));
  expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  fireEvent.click(screen.getByText("Terminal instructions"));
  expect(screen.getAllByText(new RegExp(jobId))).not.toHaveLength(0);
  expect(bodies).toHaveLength(0);
});

it("recovers from expected 503s without retrying creation or marking a job failed", async () => {
  vi.useFakeTimers();
  records.previous = view(firstId, "running");
  render(<Commands />);
  await act(async () => {});
  expect(screen.getAllByText(i18n.t("commands.states.running")).length).toBeGreaterThan(0);
  mode = "recover";
  await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
  expect(screen.getByText(i18n.t("commands.reconnecting"))).toBeInTheDocument();
  expect(screen.queryByText(i18n.t("commands.states.failed"))).toBeNull();
  mode = "ok";
  records.previous.state = "succeeded";
  await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
  expect(screen.getAllByText(i18n.t("commands.states.succeeded")).length).toBeGreaterThan(0);
  expect(bodies).toHaveLength(0);
});

it("routes Settings to Commands and back while keeping the Settings tab active", async () => {
  expect(routeFromHash("#/comandi")).toEqual({ view: "commands" });
  window.location.hash = "#/impostazioni";
  vi.stubGlobal("EventSource", MockEventSource);
  render(<App />);
  fireEvent.click(await screen.findByTestId("settings-commands"));
  await screen.findByTestId("commands-section");
  expect(screen.getByTestId("tab-settings")).toHaveClass("active");
  fireEvent.click(screen.getByTestId("commands-back"));
  await waitFor(() => expect(screen.getByTestId("settings-section")).toBeInTheDocument());
});

it("does not submit twice while preparation is in flight", async () => {
  const original = global.fetch;
  let release;
  global.fetch = vi.fn((url, options) => url === "/api/admin/proposals"
    ? new Promise((resolve) => { release = () => resolve(original(url, options)); }) : original(url, options));
  render(<Commands />);
  const run = await screen.findByRole("button", { name: "Run" });
  fireEvent.click(run); fireEvent.click(run);
  expect(global.fetch.mock.calls.filter(([url]) => url === "/api/admin/proposals")).toHaveLength(1);
  await act(async () => { release(); });
  await screen.findByRole("link", { name: "Confirm with a passkey" });
  expect(bodies).toHaveLength(1);
});

it("never offers an unsafe confirmation URL", async () => {
  const original = global.fetch;
  global.fetch = vi.fn((url, options) => url === "/api/admin/proposals"
    ? json({ ...view(), confirmation_url: "javascript:alert(1)" }, 201) : original(url, options));
  render(<Commands />);
  fireEvent.click(await screen.findByRole("button", { name: "Run" }));
  await screen.findByRole("button", { name: "Retry preparation" });
  expect(screen.queryByRole("link", { name: "Confirm with a passkey" })).toBeNull();
});

it("labels bridge evidence as reported by the user service in Italian", async () => {
  await i18n.changeLanguage("it");
  const bridge = { ...view(firstId, "succeeded"), action: "restart-bridge", evidence: "user_service_report" };
  const original = global.fetch;
  global.fetch = vi.fn((url, options) => url === "/api/admin/catalogue"
    ? json({ status: "available", execution_enabled: true, actions: [{ ...action, id: "restart-bridge" }], recent: [bridge], blocked_job_id: null })
    : original(url, options));
  render(<Commands />);
  await screen.findByRole("button", { name: "Esegui" });
  expect(screen.getAllByText(i18n.t("commands.bridgeEvidence")).length).toBeGreaterThan(0);
});

it("clears a reconciled gate and refreshes an active job that left recent history", async () => {
  records.old = view(firstId, "outcome_unknown"); blocked = jobId;
  const original = global.fetch;
  let hideHistory = false, reconciled = false;
  global.fetch = vi.fn(async (url, options) => {
    if (url === `/api/admin/proposals/${firstId}`) return json(view(firstId, reconciled ? "succeeded" : "outcome_unknown"));
    const response = await original(url, options);
    if (url === "/api/admin/catalogue" && hideHistory) return json({ ...(await response.json()), recent: [] });
    return response;
  });
  render(<Commands />);
  await screen.findByText(i18n.t("commands.blocked"));
  hideHistory = true; reconciled = true; blocked = null;
  fireEvent(window, new Event("focus"));
  const run = await screen.findByRole("button", { name: "Run" });
  await waitFor(() => expect(run).not.toBeDisabled());
  expect(screen.queryByText(i18n.t("commands.blocked"))).toBeNull();
  expect(bodies).toHaveLength(0);
});

it("does not poll while the installed app is hidden and refreshes on return", async () => {
  let visibility = "hidden";
  const descriptor = Object.getOwnPropertyDescriptor(document, "visibilityState");
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
  try {
    render(<Commands onBack={() => {}} />);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(fetch).not.toHaveBeenCalled();
    visibility = "visible";
    fireEvent(document, new Event("visibilitychange"));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  } finally {
    if (descriptor) Object.defineProperty(document, "visibilityState", descriptor);
    else delete document.visibilityState;
  }
});

it.each([
  ["en", "The bridge helper exited without a confirmed result."],
  ["it", "Il processo di supporto del bridge è terminato senza un esito confermato."],
])("shows a sanitized diagnostic in %s without claiming a restart was requested", async (language, message) => {
  await i18n.changeLanguage(language);
  const row = { ...view(firstId, "outcome_unknown"), action: "restart-bridge",
    result_code: "helper_exit_nonzero", evidence: "user_service_report" };
  vi.stubGlobal("fetch", vi.fn(() => json({ status: "available", execution_enabled: true,
    actions: [{ ...action, id: "restart-bridge" }], recent: [row], blocked_job_id: jobId })));
  render(<Commands />);
  await waitFor(() => expect(screen.getAllByText(message).length).toBeGreaterThan(0));
  expect(screen.queryByText(/Restart requested|Riavvio richiesto/)).toBeNull();
});

it("never displays an unknown private result code as text", async () => {
  const row = { ...view(firstId, "outcome_unknown"), result_code: "private_secret" };
  records.existing = row;
  render(<Commands />);
  await waitFor(() => expect(screen.getAllByText(i18n.t("commands.noDiagnostic")).length).toBeGreaterThan(0));
  expect(document.body.textContent).not.toContain("private_secret");
});
