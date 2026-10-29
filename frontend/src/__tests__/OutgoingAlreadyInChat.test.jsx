import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SessionView from "../SessionView";
import i18n from "../i18n";
import { mockFetch, transcriptOf } from "./testUtils";

const session = {
  id: "s1", agent_id: "codex", provider: "codex", entrypoint: "codex-desktop", cwd: "/p/lab", title: "Lab", state: "busy",
  reachable: true, activity_at: "2026-10-29T10:30:00Z", started_at: "2026-10-29T09:00:00Z", last_seen_at: "2026-10-29T10:30:00Z",
};
const message = (id, extra = {}) => ({
  id, question_type: "message", status: "EXECUTING", execution: "unobserved", delivery: "accepted", sender_id: "human",
  recipient_id: "codex", recipient_session_id: "s1", prompt: `messaggio ${id}`, created_at: "2026-10-29T10:30:00Z", ...extra,
});
const page = (messages) => ({ session_id: "s1", provider: "codex", before: null, end: 900, messages });

function renderView(requests, messages) {
  mockFetch([[transcriptOf("s1"), page(messages)]]);
  return render(<SessionView sessionId="s1" session={session} requests={requests} sessions={[session]} onBack={vi.fn()}
    onDecide={vi.fn()} onSend={vi.fn()} onDelivery={vi.fn()} />);
}

describe("a message that is already in the conversation is not shown a second time", () => {
  beforeEach(async () => { await i18n.changeLanguage("it"); });

  it("hides the grey 'queued in Codex' bubble once the conversation shows that message", async () => {
    renderView([message(587)], [
      { role: "user", text: "messaggio 587", at: "2026-10-29T10:31:00Z", cursor: "1:0", request_id: 587, via: "placet" },
      { role: "assistant", text: "fatto", at: "2026-10-29T10:32:00Z", cursor: "2:0" },
    ]);
    await screen.findByText("fatto");
    expect(screen.queryByTestId("outgoing-587")).toBeNull();
  });

  it("keeps it while the conversation does not have it yet", async () => {
    renderView([message(587)], [{ role: "assistant", text: "altro", at: "2026-10-29T10:29:00Z", cursor: "1:0" }]);
    await screen.findByText("altro");
    expect(screen.getByTestId("outgoing-587")).toHaveTextContent("Accodato in Codex");
  });

  it("only hides the one that is there, not another waiting behind it", async () => {
    renderView([message(587), message(588, { status: "APPROVED", delivery: "waiting" })], [
      { role: "user", text: "messaggio 587", at: "2026-10-29T10:31:00Z", cursor: "1:0", request_id: 587, via: "placet" },
    ]);
    await waitFor(() => expect(screen.queryByTestId("outgoing-587")).toBeNull());
    expect(screen.getByTestId("outgoing-588")).toBeInTheDocument();
  });
});
