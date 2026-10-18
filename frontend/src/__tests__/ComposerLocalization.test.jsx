import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import i18n, { setLanguagePreference } from "../i18n";
import Composer from "../Composer";
import AddSessionsDialog from "../AddSessionsDialog";
import App from "../App";
import { mockFetch, MockEventSource, json } from "./testUtils";

const session = { id: "current", agent_id: "claude", provider: "claude-code", state: "busy", reachable: true };
const target = { id: "target123", agent_id: "codex", provider: "codex", state: "idle", title: "Titolo originale", reachable: true };

afterEach(async () => {
  vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear();
  await setLanguagePreference("auto");
});

it.each(["en", "it"])("sends an attachment and mention unchanged in %s", async (language) => {
  await i18n.changeLanguage(language);
  const send = vi.fn().mockResolvedValue(true);
  render(<Composer session={session} sessions={[session, target]} onSend={send} />);
  const input = screen.getByLabelText(i18n.t("composer.message"));
  fireEvent.change(input, { target: { value: "@codex", selectionStart: 6 } });
  fireEvent.click(screen.getByRole("option"));
  const token = input.value.trim();
  fireEvent.change(input, { target: { value: `${token} Please review`, selectionStart: token.length + 14 } });
  fireEvent.change(screen.getByLabelText(i18n.t("composer.chooseFiles")), { target: { files: [new File(["%PDF-1.4"], "original.pdf", { type: "application/pdf" })] } });
  fireEvent.click(screen.getByRole("radio", { name: i18n.t("composer.now"), exact: true }));
  fireEvent.click(screen.getByRole("button", { name: i18n.t("composer.send"), exact: true }));
  await waitFor(() => expect(send).toHaveBeenCalledOnce());
  expect(send.mock.calls[0]).toEqual([`${token} Please review`, [{ session_id: target.id, token }], [{ name: "original.pdf", data: btoa("%PDF-1.4") }], "now"]);
  await waitFor(() => expect(input).toHaveValue(""));
});

it("keeps the draft, mention token, file and timing across language changes and send failure", async () => {
  const send = vi.fn().mockResolvedValue(false);
  render(<Composer session={session} sessions={[session, target]} onSend={send} />);
  const input = screen.getByLabelText("Messaggio per la sessione");
  fireEvent.change(input, { target: { value: "@codex", selectionStart: 6 } });
  fireEvent.click(screen.getByRole("option"));
  const text = input.value;
  fireEvent.change(screen.getByLabelText("Scegli allegati"), { target: { files: [new File(["%PDF"], "test.pdf", { type: "application/pdf" })] } });
  fireEvent.click(screen.getByRole("radio", { name: "Subito", exact: true }));
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByLabelText("Message for the session")).toHaveValue(text);
  expect(screen.getByRole("radio", { name: "Now", exact: true })).toBeChecked();
  expect(screen.getByRole("button", { name: "Remove test.pdf" })).toBeInTheDocument();
  expect(screen.getAllByRole("status").some((element) => element.textContent.includes("References: one session"))).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Send", exact: true }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Send", exact: true })).toBeEnabled());
  expect(send).toHaveBeenCalledOnce();
  expect(input).toHaveValue(text);
  expect(screen.getByRole("button", { name: "Remove test.pdf" })).toBeInTheDocument();
});

it("translates an existing file error and recovers by selecting a valid file", async () => {
  render(<Composer session={session} onSend={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Scegli allegati"), { target: { files: [new File(["x"], "x.html", { type: "text/html" })] } });
  expect(screen.getByRole("alert")).toHaveTextContent("Scegli immagini");
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByRole("alert")).toHaveTextContent("Choose JPEG");
  fireEvent.change(screen.getByLabelText("Choose attachments"), { target: { files: [new File(["%PDF"], "x.pdf", { type: "application/pdf" })] } });
  expect(screen.queryByRole("alert")).toBeNull();
});

it("keeps unsupported Now disabled for Codex and translates suspended notices", async () => {
  await i18n.changeLanguage("en");
  const { rerender } = render(<Composer session={{ ...session, provider: "codex" }} onSend={vi.fn()} />);
  expect(screen.getByRole("radio", { name: "Now", exact: true })).toBeDisabled();
  rerender(<Composer session={{ ...session, suspended_at: "now", state: "idle" }} onSend={vi.fn()} />);
  expect(screen.getByTestId("composer-suspended")).toHaveTextContent("Claude suspended");
  rerender(<Composer session={{ ...session, ended_at: "now" }} onSend={vi.fn()} />);
  expect(screen.getByLabelText("Message for the session")).toBeDisabled();
});

it.each(["en", "it"])("lets a human unpin a closed session in %s", async (language) => {
  await i18n.changeLanguage(language);
  const toggle = vi.fn();
  const closed = { ...target, ended_at: "2026-01-01", pinned: true };
  render(<AddSessionsDialog sessions={[closed]} onToggle={toggle} onClose={vi.fn()} onOpen={vi.fn()} />);
  const checkbox = screen.getByRole("checkbox", { name: /Titolo originale/ });
  expect(checkbox).toBeChecked();
  fireEvent.click(checkbox);
  expect(toggle).toHaveBeenCalledWith(closed, false);
});

it("keeps the selected provider while translating a session picker", async () => {
  render(<AddSessionsDialog sessions={[session, target]} onToggle={vi.fn()} onClose={vi.fn()} onOpen={vi.fn()} />);
  fireEvent.click(screen.getByTestId("provider-tab-codex"));
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByTestId("provider-tab-codex")).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("dialog", { name: "Add sessions" })).toHaveTextContent("Titolo originale");
});

it("shows a localized message error through the App without discarding the draft", async () => {
  await setLanguagePreference("en"); vi.stubGlobal("EventSource", MockEventSource);
  window.location.hash = "#/sessione/current";
  mockFetch([
    ["/api/requests", []], ["/api/sessions", [session]],
    [(url) => url.startsWith("/api/sessions/current/transcript?"), { messages: [], before: null, end: 0 }],
    ["/api/sessions/current/messages", () => json({ code: "mention.unavailable", detail: "diagnostic only" }, 409)],
  ]);
  render(<App />);
  const input = await screen.findByLabelText("Message for the session");
  fireEvent.change(input, { target: { value: "My draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Send", exact: true }));
  await waitFor(() => expect(screen.getByTestId("feedback-banner")).toHaveTextContent("A mentioned session is no longer available"));
  expect(input).toHaveValue("My draft");
  await act(async () => { await i18n.changeLanguage("it"); });
  expect(screen.getByTestId("feedback-banner")).toHaveTextContent("Una sessione menzionata non è più disponibile");
  expect(screen.queryByText("diagnostic only")).toBeNull();
});
