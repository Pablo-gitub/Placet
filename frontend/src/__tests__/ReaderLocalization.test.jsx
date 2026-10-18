import React from "react";
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import i18n, { setLanguagePreference } from "../i18n";
import SessionView from "../SessionView";
import { useTranscript } from "../useTranscript";
import App from "../App";
import CodeBlock from "../CodeBlock";
import { json, mockFetch, MockEventSource, paramsOf, transcriptOf } from "./testUtils";

const session = { id: "c1", title: "Original title", agent_id: "claude", provider: "claude-code", state: "idle", reachable: true };
const page = { before: null, end: 20, messages: [
  { cursor: "1:0", role: "user", text: "Messaggio originale", via: "placet" },
  ...["read", "edit", "search"].map((kind, i) => ({ cursor: `${i+2}:0`, role: "action", kind, tool: "Read", detail: kind === "search" ? "TODO" : "/project/file.py" })),
  { cursor: "6:0", role: "compact", text: "" },
  { cursor: "7:0", role: "placet", text: "Original message ".repeat(40) },
  { cursor: "8:0", role: "media", text: "Shared file", label_key: "shared_file" },
  { cursor: "9:0", role: "media", text: "Do not translate this caption" },
] };
const renderReader = (props = {}) => render(<SessionView sessionId="c1" session={session} requests={[]} sessions={[session]} onBack={vi.fn()} onDecide={vi.fn()} {...props} />);

afterEach(async () => { vi.unstubAllGlobals(); vi.restoreAllMocks(); localStorage.clear(); await setLanguagePreference("auto"); });

it.each(["en", "it"])("localizes reader controls but preserves conversation data in %s", async language => {
  await i18n.changeLanguage(language);
  mockFetch([[transcriptOf("c1"), page]]);
  renderReader();
  expect(await screen.findByText("Messaggio originale")).toBeInTheDocument();
  const actions = screen.getByTestId("chat-actions");
  fireEvent.click(within(actions).getByRole("button"));
  expect(actions).toHaveTextContent(i18n.t("actions.read", { name: "file.py" }));
  expect(actions).toHaveTextContent(i18n.t("actions.search", { detail: "TODO" }));
  fireEvent.click(screen.getByRole("button", { name: i18n.t("chat.showAll") }));
  await act(async () => { await i18n.changeLanguage(language === "en" ? "it" : "en"); });
  expect(actions).toHaveTextContent(i18n.t("actions.edit", { name: "file.py" }));
  expect(screen.getByRole("button", { name: i18n.t("chat.showLess") })).toBeInTheDocument();
  expect(screen.getByText("Do not translate this caption")).toBeInTheDocument();
  expect(screen.getByText(i18n.t("chat.sharedFile"))).toBeInTheDocument();
  expect(screen.getByTestId("session-view-name")).toHaveTextContent("Original title");
});

it("translates a visible reader error and retries without rendering diagnostics", async () => {
  let failed = true;
  mockFetch([[transcriptOf("c1"), () => failed ? json({ code: "bridge.offline", detail: "private diagnostic" }, 503) : json(page)]]);
  renderReader();
  expect(await screen.findByTestId("chat-error")).not.toHaveTextContent("private diagnostic");
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByTestId("chat-error")).toHaveTextContent("The PC is not responding");
  failed = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Messaggio originale")).toBeInTheDocument();
});

it("keeps loaded messages and offers a localized retry when older pages fail", async () => {
  let fail = true;
  mockFetch([
    [(url) => transcriptOf("c1")(url) && paramsOf(url).has("before"), () => fail ? json({ code: "bridge.timeout" }, 504) : json({ before: null, end: 20, messages: [{ cursor: "0:0", role: "user", text: "Older original" }] })],
    [transcriptOf("c1"), { ...page, before: 10 }],
  ]);
  renderReader();
  fireEvent.click(await screen.findByTestId("load-older"));
  expect(await screen.findByTestId("older-error")).toHaveTextContent(i18n.t("errors.bridgeTimeout"));
  expect(screen.getByText("Messaggio originale")).toBeInTheDocument();
  await act(async () => { await i18n.changeLanguage("en"); });
  fail = false;
  fireEvent.click(within(screen.getByTestId("older-error")).getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Older original")).toBeInTheDocument();
  expect(screen.queryByTestId("older-error")).toBeNull();
});

it("copies the exact code and updates copy controls across languages", async () => {
  const writeText = vi.fn().mockResolvedValue();
  vi.stubGlobal("navigator", { languages: ["en-US"], clipboard: { writeText } });
  render(<CodeBlock className="language-python">{'print("ciao")\n'}</CodeBlock>);
  fireEvent.click(screen.getByLabelText("Copia il codice"));
  await waitFor(() => expect(screen.getByTestId("code-copy")).toHaveTextContent("Copiato"));
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByLabelText("Copy code")).toHaveTextContent("Copied");
  expect(writeText).toHaveBeenCalledWith('print("ciao")');
});

it("asks for localized confirmation before retrying an uncertain message", async () => {
  await setLanguagePreference("en"); vi.stubGlobal("EventSource", MockEventSource);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  window.location.hash = "#/sessione/c1";
  const req = { id: 4, question_type: "message", prompt: "Original outgoing", recipient_session_id: "c1", status: "DELIVERING", delivery: "uncertain" };
  const fetch = mockFetch([
    ["/api/requests", [req]], ["/api/sessions", [session]], [transcriptOf("c1"), page],
    ["/api/requests/4/delivery/retry", () => json({ code: "delivery.retry_unavailable" }, 409)],
  ]);
  render(<App />);
  fireEvent.click(await screen.findByTestId("retry-4"));
  expect(confirm).toHaveBeenCalledWith("The message may already have arrived. Send it again?");
  await waitFor(() => expect(screen.getByTestId("feedback-banner")).toHaveTextContent("Delivery is no longer uncertain"));
  expect(fetch).toHaveBeenCalledWith("/api/requests/4/delivery/retry", { method: "POST" });
  expect(screen.getByText("Original outgoing")).toBeInTheDocument();
});

it("confirms queue-hold cancellation in the active language", async () => {
  await i18n.changeLanguage("en");
  mockFetch([[transcriptOf("c1"), page]]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false), delivery = vi.fn();
  renderReader({ requests: [{ id: 5, recipient_session_id: "c1", status: "STALLED", execution: "started" }], onDelivery: delivery });
  fireEvent.click(screen.getByTestId("cancel-5"));
  expect(confirm.mock.calls[0][0]).toContain("The agent may continue working");
  expect(delivery).not.toHaveBeenCalled();
});


it("clears the previous session metadata while the next conversation loads", async () => {
  let fail;
  mockFetch([
    [transcriptOf("c1"), { messages: [], before: null, end: 0, meta: { model: "original-model" } }],
    [transcriptOf("c2"), () => new Promise((resolve, reject) => { fail = reject; })],
  ]);
  const { result, rerender } = renderHook(({ id }) => useTranscript(id), { initialProps: { id: "c1" } });
  await waitFor(() => expect(result.current.meta).toEqual({ model: "original-model" }));
  rerender({ id: "c2" });
  expect(result.current.meta).toBeNull();
  await act(async () => { fail(new Error("offline")); });
  expect(result.current.status).toBe("error");
  expect(result.current.meta).toBeNull();
});
