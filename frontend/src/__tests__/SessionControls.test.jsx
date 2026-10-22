import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import SessionControls from "../SessionControls";
import i18n from "../i18n";

const session = (controls, over = {}) => ({ id: "s1", provider: "claude-code", state: "idle", controls, ...over });
const SUPPORTED = { compact: "supported", effort: "supported" };

function serve(status = 200, body = { ok: true }) {
  const calls = [];
  global.fetch = vi.fn((url, opts = {}) => {
    calls.push({ url, method: opts.method, body: opts.body && JSON.parse(opts.body) });
    return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
  });
  return calls;
}

const open = () => fireEvent.click(screen.getByTestId("session-controls-toggle"));

describe("session controls menu", () => {
  afterEach(() => vi.restoreAllMocks());

  it("compacts a supported idle session", async () => {
    const calls = serve();
    render(<SessionControls session={session(SUPPORTED)} />);
    open();
    fireEvent.click(screen.getByTestId("control-compact"));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ url: "/api/sessions/s1/compact", method: "POST" });
    expect(await screen.findByTestId("control-done")).toBeInTheDocument();
  });

  it("sets an effort level chosen from the provider's list", async () => {
    const calls = serve();
    render(<SessionControls session={session(SUPPORTED)} />);
    open();
    const picker = screen.getByTestId("control-effort-level");
    expect([...picker.options].map((o) => o.value)).toEqual(["low", "medium", "high", "xhigh", "max"]);
    fireEvent.change(picker, { target: { value: "xhigh" } });
    fireEvent.click(screen.getByTestId("control-effort"));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ url: "/api/sessions/s1/effort", method: "POST", body: { level: "xhigh" } });
  });

  it("is disabled with the reason when the control is PC-only or unsupported", () => {
    const { unmount } = render(<SessionControls session={session({ compact: "pc_only", effort: "pc_only" })} />);
    open();
    expect(screen.getByTestId("control-compact")).toBeDisabled();
    expect(screen.getByTestId("control-effort")).toBeDisabled();
    expect(screen.getByTestId("control-explanation")).toHaveTextContent(i18n.t("controls.pcOnly"));
    unmount();
    render(<SessionControls session={session({ compact: "unsupported", effort: "unsupported" }, { provider: "codex" })} />);
    open();
    expect(screen.getByTestId("control-compact")).toBeDisabled();
    expect(screen.getByTestId("control-explanation")).toHaveTextContent(i18n.t("controls.unsupported"));
  });

  it("is disabled while the session is working", () => {
    render(<SessionControls session={session(SUPPORTED, { state: "busy" })} />);
    open();
    expect(screen.getByTestId("control-compact")).toBeDisabled();
    expect(screen.getByTestId("control-explanation")).toHaveTextContent(i18n.t("controls.busy"));
  });

  it("shows a translated message when the server refuses", async () => {
    serve(409, { code: "session_command.user_typing", params: {}, detail: "raw server text" });
    render(<SessionControls session={session(SUPPORTED)} />);
    open();
    fireEvent.click(screen.getByTestId("control-compact"));
    const error = await screen.findByTestId("control-error");
    expect(error).toHaveTextContent(i18n.t("errors.commandUserTyping"));
    expect(error).not.toHaveTextContent("raw server text");
  });

  it("tells an uncertain outcome apart and asks before repeating it", async () => {
    const calls = serve(502, { code: "session_command.uncertain", params: { control_id: 4 }, detail: "x" });
    render(<SessionControls session={session(SUPPORTED)} />);
    open();
    fireEvent.click(screen.getByTestId("control-compact"));
    expect(await screen.findByTestId("control-error")).toHaveTextContent(i18n.t("errors.commandUncertain"));
    expect(calls).toHaveLength(1);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByTestId("control-compact"));
    expect(confirm).toHaveBeenCalledWith(i18n.t("controls.confirmRepeat"));
    expect(calls).toHaveLength(1);                     // declined: nothing sent
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByTestId("control-compact"));
    await waitFor(() => expect(calls).toHaveLength(2));  // confirmed: sent again
  });

  it("does not ask for confirmation after a clean refusal", async () => {
    const calls = serve(409, { code: "session_command.not_idle", params: {}, detail: "x" });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<SessionControls session={session(SUPPORTED)} />);
    open();
    fireEvent.click(screen.getByTestId("control-compact"));
    await screen.findByTestId("control-error");
    fireEvent.click(screen.getByTestId("control-compact"));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(confirm).not.toHaveBeenCalled();
  });

  it("translates the pane and pending refusals", async () => {
    for (const [code, key] of [["session.pane_stale", "errors.paneStale"], ["session.pane_ambiguous", "errors.paneAmbiguous"],
      ["session.control_pending", "errors.controlPending"]]) {
      serve(409, { code, params: {}, detail: "raw" });
      const { unmount } = render(<SessionControls session={session(SUPPORTED)} />);
      open();
      fireEvent.click(screen.getByTestId("control-compact"));
      expect(await screen.findByTestId("control-error")).toHaveTextContent(i18n.t(key));
      unmount();
    }
  });

  it("warns that some effort levels persist as the default for future sessions", () => {
    render(<SessionControls session={session(SUPPORTED)} />);
    open();
    const note = screen.getByTestId("control-effort-note");
    expect(note).toHaveTextContent(i18n.t("controls.effortPersists"));
    expect(note.querySelector("a")).toHaveAttribute(
      "href", "https://code.claude.com/docs/en/model-config#adjust-effort-level");
  });

  it("translates a refusal when tmux could not check the input", async () => {
    serve(409, { code: "session_command.check_failed", params: {}, detail: "raw" });
    render(<SessionControls session={session(SUPPORTED)} />);
    open();
    fireEvent.click(screen.getByTestId("control-compact"));
    expect(await screen.findByTestId("control-error")).toHaveTextContent(i18n.t("errors.commandCheckFailed"));
  });

  it("explains a terminal that no longer matches the session, in one message", async () => {
    for (const code of ["server_mismatch", "agent_gone", "agent_not_in_pane", "identity_unknown"]) {
      serve(409, { code: `session_command.${code}`, params: {}, detail: "raw" });
      const { unmount } = render(<SessionControls session={session(SUPPORTED)} />);
      open();
      fireEvent.click(screen.getByTestId("control-compact"));
      expect(await screen.findByTestId("control-error")).toHaveTextContent(i18n.t("errors.commandIdentity"));
      unmount();
    }
  });

  it("says to confirm on the PC when a dialog is waiting, instead of claiming success", async () => {
    serve(200, { ok: true, command: "/effort high", state: "needs_confirmation" });
    render(<SessionControls session={session(SUPPORTED)} />);
    open();
    fireEvent.click(screen.getByTestId("control-effort"));
    expect(await screen.findByTestId("control-needs-confirmation")).toHaveTextContent(i18n.t("controls.needsConfirmation"));
    expect(screen.queryByTestId("control-done")).toBeNull();
    expect(screen.queryByTestId("control-error")).toBeNull();
  });

  it("translates the refusals of the hardened path", async () => {
    for (const [code, key] of [["session_command.expired", "errors.commandExpired"],
      ["session_command.unknown_job", "errors.commandBridgeOld"], ["session.has_request_running", "errors.sessionRequestRunning"],
      ["bridge.not_taken", "errors.bridgeNotTaken"]]) {
      serve(409, { code, params: {}, detail: "raw" });
      const { unmount } = render(<SessionControls session={session(SUPPORTED)} />);
      open();
      fireEvent.click(screen.getByTestId("control-compact"));
      expect(await screen.findByTestId("control-error")).toHaveTextContent(i18n.t(key));
      unmount();
    }
  });

  it("renders nothing for a session without control information", () => {
    const { container } = render(<SessionControls session={{ id: "s1", provider: "claude-code" }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("has both languages", () => {
    for (const lng of ["en", "it"]) {
      for (const key of ["controls.title", "controls.compact", "controls.effort", "controls.apply", "controls.done",
        "controls.pcOnly", "controls.unsupported", "controls.busy", "errors.commandUserTyping", "errors.sessionBusy", "errors.commandUncertain", "errors.commandEnterFailed", "errors.commandCheckFailed", "errors.commandIdentity", "controls.effortPersists", "controls.needsConfirmation", "controls.effortDocs",
        "errors.paneStale", "errors.paneAmbiguous", "errors.controlPending", "controls.confirmRepeat"]) {
        expect(i18n.getFixedT(lng)(key)).not.toBe(key);
      }
    }
  });
});
