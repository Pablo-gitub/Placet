import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import i18n, { setLanguagePreference } from "../i18n";
import { apiErrorMessage } from "../i18n/errors";
import RequestCard from "../RequestCard";
import App from "../App";
import { json, mockFetch, MockEventSource } from "./testUtils";

const now = "2026-10-18T10:00:00Z";
const sessions = [
  { id: "g1", agent_id: "gemini", provider: "antigravity", state: "busy", title: "Reviewer", reachable: true },
  { id: "g2", agent_id: "gemini", provider: "antigravity", state: "idle", title: "Tester", reachable: true },
];
const prompt = { id: 101, sender_id: "claude", recipient_id: "gemini", question_type: "prompt_review", prompt: "Rivedi questo codice",
  context: "Human context stays unchanged", recipient_session_id: "g1", prompt_source: "review.md", status: "PENDING", priority: "urgent", options: [], created_at: now };

afterEach(async () => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  await setLanguagePreference("auto");
});

it("preserves the prompt edit, target, timing and return policy across language changes", async () => {
  const decide = vi.fn();
  render(<RequestCard req={prompt} sessions={sessions} onDecide={decide} />);
  fireEvent.click(screen.getByRole("button", { name: "Modifica" }));
  fireEvent.change(screen.getByLabelText("Prompt per gemini"), { target: { value: "My edited prompt" } });
  fireEvent.click(screen.getByLabelText("Subito · nel turno in corso"));
  fireEvent.click(screen.getByLabelText("Consenti anche il ritorno automatico della risposta"));
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByLabelText("Prompt for gemini")).toHaveValue("My edited prompt");
  expect(screen.getByLabelText("Now · during the current turn")).toBeChecked();
  expect(screen.getByLabelText("Also allow the reply to return automatically")).toBeChecked();
  expect(screen.getByText(prompt.context)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect(screen.getByTestId("prompt-preview-101")).toHaveTextContent("My edited prompt");
  fireEvent.click(screen.getByRole("button", { name: "Approve and send" }));
  expect(decide).toHaveBeenCalledWith(101, { status: "APPROVED", selected_options: [], edited_prompt: "My edited prompt",
    recipient_session_id: "g1", delivery_mode: "now", allow_auto_return: true });
});

it.each(["en", "it"])("submits a single-choice Other answer unchanged in %s", async (language) => {
  await i18n.changeLanguage(language);
  const decide = vi.fn();
  render(<RequestCard req={{ ...prompt, recipient_id: "human", question_type: "single_choice", options: ["Opzione originale"] }} onDecide={decide} />);
  expect(screen.getByRole("button", { name: i18n.t("requests.answer") })).toBeDisabled();
  expect(screen.getByLabelText("Opzione originale")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText(i18n.t("requests.other")));
  fireEvent.change(screen.getByLabelText(i18n.t("requests.otherLabel")), { target: { value: "Risposta personale" } });
  fireEvent.click(screen.getByRole("button", { name: i18n.t("requests.answer") }));
  expect(decide).toHaveBeenCalledWith(101, { status: "APPROVED", selected_options: [], custom_text: "Risposta personale" });
});

it("preserves multiple selections and free text, and resets Now for a different target", async () => {
  const decide = vi.fn();
  const { unmount } = render(<RequestCard req={{ ...prompt, recipient_id: "human", question_type: "multi_choice", options: ["A", "B"] }} onDecide={decide} />);
  fireEvent.click(screen.getByLabelText("A"));
  fireEvent.click(screen.getByLabelText("Altro…"));
  fireEvent.change(screen.getByLabelText("Risposta Altro"), { target: { value: "C" } });
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByLabelText("A")).toBeChecked();
  expect(screen.getByLabelText("Other answer")).toHaveValue("C");
  fireEvent.click(screen.getByRole("button", { name: "Answer" }));
  expect(decide).toHaveBeenCalledWith(101, { status: "APPROVED", selected_options: ["A"], custom_text: "C" });
  unmount();
  render(<RequestCard req={prompt} sessions={sessions} onDecide={decide} />);
  fireEvent.click(screen.getByLabelText("Now · during the current turn"));
  fireEvent.change(screen.getByLabelText("Receiving session of gemini:"), { target: { value: "g2" } });
  fireEvent.click(screen.getByRole("button", { name: "Approve and send" }));
  expect(decide.mock.lastCall[1]).not.toHaveProperty("delivery_mode");
});

it.each(["en", "it"])("approves a prompt through the App in %s", async (language) => {
  await setLanguagePreference(language);
  vi.stubGlobal("EventSource", MockEventSource);
  window.location.hash = "#/";
  let pending = true;
  mockFetch([
    ["/api/requests", () => pending ? [prompt] : []], ["/api/sessions", sessions],
    ["/api/requests/101/decision", (_url, options) => {
      expect(JSON.parse(options.body)).toEqual({ status: "APPROVED", selected_options: [], recipient_session_id: "g1" });
      pending = false;
      return { ...prompt, status: "APPROVED" };
    }],
  ]);
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: i18n.t("requests.approveSend") }));
  await waitFor(() => expect(screen.getByTestId("feedback-banner")).toHaveTextContent(i18n.t("requests.sent", { id: 101 })));
});

it("rejects a confirmation with its note through the App", async () => {
  await setLanguagePreference("en");
  vi.stubGlobal("EventSource", MockEventSource);
  window.location.hash = "#/";
  mockFetch([
    ["/api/requests", [{ ...prompt, recipient_id: "human", question_type: "confirmation" }]], ["/api/sessions", []],
    ["/api/requests/101/decision", (_url, options) => {
      expect(JSON.parse(options.body)).toEqual({ status: "REJECTED", selected_options: [], custom_text: "Not yet" });
      return {};
    }],
  ]);
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Note for the agent"), { target: { value: "Not yet" } });
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() => expect(screen.getByTestId("feedback-banner")).toHaveTextContent("Request #101 rejected."));
});

it("shows a localized conflict without discarding a prompt edit", async () => {
  await setLanguagePreference("en");
  vi.stubGlobal("EventSource", MockEventSource);
  window.location.hash = "#/";
  mockFetch([
    ["/api/requests", [prompt]], ["/api/sessions", sessions],
    ["/api/requests/101/decision", () => json({ code: "decision.not_pending", params: { id: 101 }, detail: "diagnostic only" }, 409)],
  ]);
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Prompt for gemini"), { target: { value: "Keep this draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Approve and send" }));
  await waitFor(() => expect(screen.getByTestId("feedback-banner")).toHaveTextContent("Request #101 is no longer awaiting approval."));
  expect(screen.getByLabelText("Prompt for gemini")).toHaveValue("Keep this draft");
  expect(screen.queryByText("diagnostic only")).toBeNull();
});

it("does not let error parameters override the selected language", async () => {
  await i18n.changeLanguage("en");
  const message = apiErrorMessage({ code: "decision.not_pending", params: { id: 42, lng: "it", defaultValue: "untrusted" } }, 409, i18n.t.bind(i18n));
  expect(message).toContain("Request #42");
  expect(message).not.toContain("untrusted");
});
