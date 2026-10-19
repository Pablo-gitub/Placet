import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";

const root = resolve(process.cwd(), "../admin_executor/web_static");
const source = readFileSync(resolve(root, "app.js"), "utf8");
const html = readFileSync(resolve(root, "index.html"), "utf8");
const messages = Object.fromEntries(["en", "it"].map((locale) => [locale, JSON.parse(readFileSync(resolve(root, `${locale}.json`), "utf8"))]));
const id = "a".repeat(32);
const operation = { proposal: { action: "restart-backend", parameters: {}, catalogue_revision: 1 },
  handler_revision: 1, installed_commit: null, artifact_sha256: null, summary: "Restart placet-backend.service." };
let view;
let getCredential;
let challengeOperation;
let offline;

beforeEach(() => {
  document.documentElement.innerHTML = html.replace(/<!doctype html>/i, "").replace(/<\/?html[^>]*>/g, "");
  history.replaceState(null, "", `/?proposal=${id}`);
  localStorage.clear();
  localStorage.setItem("placet-admin.language", "en");
  view = { id, action: "restart-backend", operation, enabled: true, state: "awaiting_confirmation",
    job_id: null, evidence: "system_service_health", return_url: "https://placet.example.test/#/comandi" };
  challengeOperation = operation;
  offline = false;
  const buffer = new Uint8Array([1, 2]).buffer;
  getCredential = vi.fn().mockResolvedValue({ id: "phone", rawId: buffer, type: "public-key", response: {
    clientDataJSON: buffer, authenticatorData: buffer, signature: buffer, userHandle: null,
  } });
  Object.defineProperty(navigator, "credentials", { configurable: true, value: { get: getCredential, create: vi.fn() } });
  vi.stubGlobal("PublicKeyCredential", class {});
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("fetch", vi.fn(async (path) => {
    let body;
    if (path.startsWith("/assets/")) body = messages[path.includes("/it.") ? "it" : "en"];
    else if (offline) return { ok: false, json: async () => ({ code: "administrative_unavailable" }) };
    else if (path.endsWith("/challenge")) body = { id: "b".repeat(32), operation: challengeOperation,
      publicKey: { challenge: "YQ", rpId: "admin.example.test", allowCredentials: [{ id: "Yg", type: "public-key" }], userVerification: "required" } };
    else if (path.endsWith("/authorize")) body = view = { ...view, state: "authorized", job_id: "job" };
    else body = view;
    return { ok: true, json: async () => body };
  }));
});
afterEach(() => { window.dispatchEvent(new Event("pagehide")); vi.unstubAllGlobals(); });
async function open() {
  window.eval(source);
  await waitFor(() => expect(document.querySelector("#confirm")?.hidden).toBe(false));
}

describe("protected administrative confirmation page", () => {
  it("shows the trusted action before signing and recovers its durable job", async () => {
    await open();
    expect(document.querySelector("#operation-summary").textContent).toContain("placet-backend.service");
    expect(getCredential).not.toHaveBeenCalled();
    fireEvent.click(document.querySelector("#confirm"));
    await waitFor(() => expect(document.querySelector("#command-state").textContent).toContain(messages.en.authorized));
    expect(getCredential).toHaveBeenCalledTimes(1);
    const options = getCredential.mock.calls[0][0].publicKey;
    expect(options.userVerification).toBe("required");
    expect(options.challenge).toBeInstanceOf(Uint8Array);
    expect(document.querySelector("#return-link").href).toBe(view.return_url);
    expect(document.querySelector("#confirm").hidden).toBe(true);
    expect(document.querySelectorAll("[style]")).toHaveLength(0);
  });
  it("does not request a signature if the operation has changed", async () => {
    await open();
    challengeOperation = { ...operation, handler_revision: 2 };
    fireEvent.click(document.querySelector("#confirm"));
    await waitFor(() => expect(document.querySelector("#command-feedback").textContent).toBe(messages.en.operationChanged));
    expect(getCredential).not.toHaveBeenCalled();
  });
  it("explains uncertain outcomes in Italian and has no executable button", async () => {
    localStorage.setItem("placet-admin.language", "it");
    view = { ...view, state: "outcome_unknown", job_id: "job" };
    window.eval(source);
    await waitFor(() => expect(document.querySelector("#command-state")?.textContent).toBe(messages.it.outcome_unknown));
    expect(document.querySelector("#confirm").hidden).toBe(true);
    expect(getCredential).not.toHaveBeenCalled();
  });
  it("does not offer authorization while the broker is unavailable", async () => {
    offline = true;
    window.eval(source);
    await waitFor(() => expect(document.querySelector("#command-feedback")?.textContent).toBe(messages.en.commandUnavailable));
    expect(document.querySelector("#confirm").hidden).toBe(true);
    expect(getCredential).not.toHaveBeenCalled();
  });
});

it("explains expired proposals without requesting another signature", async () => {
  view = { ...view, state: "expired", enabled: false };
  window.eval(source);
  await waitFor(() => expect(document.querySelector("#command-state")?.textContent).toBe(messages.en.expired));
  expect(document.querySelector("#confirm").hidden).toBe(true);
  expect(getCredential).not.toHaveBeenCalled();
});
