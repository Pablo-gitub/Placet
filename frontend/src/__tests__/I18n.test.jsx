import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import i18n, { setLanguagePreference } from "../i18n";
import { readPreference, resolveLanguage, LANGUAGE_KEY } from "../i18n/locale";
import { formatDateTime, formatNumber } from "../i18n/formatters";
import { apiErrorMessage } from "../i18n/errors";
import { sessionStatus } from "../sessionInfo";
import RequestCard from "../RequestCard";
import App from "../App";
import en from "../i18n/locales/en.json";
import italian from "../i18n/locales/it.json";
import { json, mockFetch, MockEventSource } from "./testUtils";

const reply = { id: 90, sender_id: "claude", recipient_id: "codex", status: "COMPLETED",
  prompt: "Review this code", response: { content: "Original <script>literal text</script>" }, created_at: "2026-10-18T10:00:00Z" };

afterEach(async () => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  await setLanguagePreference("auto");
});

it("resolves saved preference, regional browser languages and English fallback", () => {
  expect(resolveLanguage("it", ["en-US"])).toBe("it");
  expect(resolveLanguage("auto", ["fr-FR", "it-IT"])).toBe("it");
  expect(resolveLanguage("bad", ["fr-FR"])).toBe("en");
  expect(resolveLanguage(null, [])).toBe("en");
  expect(readPreference({ getItem: () => "broken" })).toBe("auto");
  expect(readPreference({ getItem: () => { throw new Error("denied"); } })).toBe("auto");
});

it("persists an explicit preference and supports unavailable storage", async () => {
  await setLanguagePreference("en");
  expect(localStorage.getItem(LANGUAGE_KEY)).toBe("en");
  expect(document.documentElement.lang).toBe("en");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
  await setLanguagePreference("it");
  expect(i18n.resolvedLanguage).toBe("it");
  await setLanguagePreference("auto");
  expect(localStorage.getItem(LANGUAGE_KEY)).toBeNull();
});

it("keeps catalog keys and interpolation parameters in sync", () => {
  const flatten = (catalog, prefix = "") => Object.fromEntries(Object.entries(catalog).flatMap(([key, value]) =>
    typeof value === "string" ? [[`${prefix}${key}`, value]] : Object.entries(flatten(value, `${prefix}${key}.`))));
  const english = flatten(en), translated = flatten(italian);
  expect(Object.keys(english).sort()).toEqual(Object.keys(translated).sort());
  for (const key of Object.keys(english)) {
    const params = (text) => [...text.matchAll(/{{(\w+)}}/g)].map((match) => match[1]).sort();
    expect(params(english[key]), key).toEqual(params(translated[key]));
    expect(translated[key].trim(), key).not.toBe("");
  }
});

it("uses the English fallback for missing Italian keys", () => {
  i18n.addResource("en", "translation", "testEnglishOnly", "English fallback");
  expect(i18n.t("testEnglishOnly")).toBe("English fallback");
  delete i18n.getResourceBundle("en", "translation").testEnglishOnly;
});

it("refreshes helper output and formatting when the language changes", async () => {
  expect(sessionStatus({ suspended_at: "now" }).label).toBe("Sospesa");
  expect(formatNumber(1.5)).toContain(",");
  await i18n.changeLanguage("en");
  expect(sessionStatus({ suspended_at: "now" }).label).toBe("Suspended");
  expect(formatNumber(1.5)).toContain(".");
  expect(formatDateTime("2026-10-18T10:00:00Z", { timeZone: "UTC", hour: "2-digit", hourCycle: "h23" })).toContain("10");
});

it("changes a reply approval card without losing its edited content", async () => {
  const decide = vi.fn();
  render(<RequestCard req={reply} onDecide={decide} />);
  fireEvent.change(screen.getByLabelText("Risposta da inoltrare"), { target: { value: "My edited reply" } });
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByLabelText("Reply to forward")).toHaveValue("My edited reply");
  fireEvent.click(screen.getByRole("button", { name: "Forward reply" }));
  expect(decide).toHaveBeenCalledWith(90, { return_decision: true, approve: true, edited_response: "My edited reply" });
  expect(reply.response.content).toContain("<script>");
});

it.each(["en", "it"])("forwards a reply through the App in %s", async (language) => {
  await setLanguagePreference(language);
  vi.stubGlobal("EventSource", MockEventSource);
  window.location.hash = "#/";
  let pending = true;
  const fetch = mockFetch([
    ["/api/requests", () => pending ? [reply] : [{ ...reply, return_decision: "approved" }]],
    ["/api/sessions", []],
    ["/api/requests/90/return-decision", (_url, options) => {
      expect(JSON.parse(options.body).edited_response).toBe(reply.response.content);
      pending = false;
      return reply;
    }],
  ]);
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: i18n.t("return.approve") }));
  await waitFor(() => expect(screen.getByTestId("feedback-banner")).toHaveTextContent(i18n.t("return.approved")));
  expect(fetch).toHaveBeenCalledWith("/api/requests/90/return-decision", expect.objectContaining({ method: "POST" }));
});

it("handles known errors, validation arrays and unknown failures safely", async () => {
  await i18n.changeLanguage("en");
  expect(apiErrorMessage({ code: "return.unavailable", detail: "raw Italian" }, 409, i18n.t.bind(i18n))).toContain("already been decided");
  expect(apiErrorMessage({ detail: [{ msg: "invalid" }] }, 422, i18n.t.bind(i18n))).toBe(en.errors.validation);
  expect(apiErrorMessage(null, 500, i18n.t.bind(i18n))).toBe("The operation failed (500).");
  expect(apiErrorMessage({ code: "__proto__" }, 500, i18n.t.bind(i18n))).toBe("The operation failed (500).");
});

it("shows localized API failure in the real reply approval UI", async () => {
  await i18n.changeLanguage("en");
  vi.stubGlobal("EventSource", MockEventSource);
  window.location.hash = "#/";
  mockFetch([
    ["/api/requests", [reply]], ["/api/sessions", []],
    ["/api/requests/90/return-decision", () => json({ code: "return.unavailable", detail: "not for display" }, 409)],
  ]);
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Forward reply" }));
  await waitFor(() => expect(screen.getByTestId("feedback-banner")).toHaveTextContent(en.errors.returnUnavailable));
  expect(screen.queryByText("not for display")).toBeNull();
  expect(screen.getByLabelText("Reply to forward")).toHaveValue(reply.response.content);
});

it("selects plural forms for each supported language", async () => {
  expect(i18n.t("home.pendingQuestions", { count: 1 })).toBe("1 domanda in attesa");
  expect(i18n.t("home.pendingQuestions", { count: 2 })).toBe("2 domande in attesa");
  await i18n.changeLanguage("en");
  expect(i18n.t("home.pendingQuestions", { count: 1 })).toBe("1 question waiting");
  expect(i18n.t("home.pendingQuestions", { count: 2 })).toBe("2 questions waiting");
});

it("changes language through the visible selector and can withhold a reply", async () => {
  vi.stubGlobal("EventSource", MockEventSource);
  window.location.hash = "#/";
  mockFetch([
    ["/api/requests", [reply]], ["/api/sessions", []],
    ["/api/requests/90/return-decision", (_url, options) => {
      expect(JSON.parse(options.body).approve).toBe(false);
      return { ...reply, return_decision: "rejected" };
    }],
  ]);
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Lingua"), { target: { value: "en" } });
  expect(await screen.findByRole("button", { name: "Do not forward" })).toBeInTheDocument();
  expect(localStorage.getItem(LANGUAGE_KEY)).toBe("en");
  fireEvent.click(screen.getByRole("button", { name: "Do not forward" }));
  await waitFor(() => expect(screen.getByTestId("feedback-banner")).toHaveTextContent(en.return.rejected));
});
