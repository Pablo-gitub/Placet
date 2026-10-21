import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import Composer from "../Composer";
import SessionView from "../SessionView";
import { mockFetch, transcriptOf } from "./testUtils";

const session = {
  id: "s1",
  agent_id: "claude",
  provider: "claude-code",
  reachable: true,
  activity_at: "2026-10-16T10:00:00Z",
  started_at: "2026-10-16T09:00:00Z",
};

const latest = {
  session_id: "s1",
  provider: "claude-code",
  before: 500,
  end: 900,
  messages: [{ role: "user", text: "ciao", at: "2026-10-16T09:58:00Z", cursor: "500:0" }],
};

describe("Composer scheduled messages (RF-65)", () => {
  it("renders the Programma control and converts local time to UTC on send", async () => {
    const send = vi.fn().mockResolvedValue(true);
    render(<Composer session={session} onSend={send} />);

    expect(screen.getByTestId("composer-schedule")).toBeInTheDocument();
    const scheduleInput = screen.getByTestId("composer-schedule-input");
    expect(scheduleInput).toBeInTheDocument();

    // Type text and set schedule time
    fireEvent.change(screen.getByTestId("composer-input"), { target: { value: "Messaggio programmato" } });
    fireEvent.change(scheduleInput, { target: { value: "2026-10-25T15:30" } });

    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(send).toHaveBeenCalledOnce());

    // Arguments: (text, mentions, attachments, mode, scheduledAt)
    expect(send.mock.calls[0][0]).toBe("Messaggio programmato");
    const sentScheduledAt = send.mock.calls[0][4];
    expect(sentScheduledAt).toBeTruthy();
    // Converting local 2026-10-25T15:30 to Date and to ISO should match
    expect(sentScheduledAt).toBe(new Date("2026-10-25T15:30").toISOString());
  });

  it("allows clearing the scheduled time before sending", async () => {
    const send = vi.fn().mockResolvedValue(true);
    render(<Composer session={session} onSend={send} />);

    const scheduleInput = screen.getByTestId("composer-schedule-input");
    fireEvent.change(scheduleInput, { target: { value: "2026-10-25T15:30" } });
    expect(screen.getByTestId("composer-schedule-clear")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("composer-schedule-clear"));
    expect(scheduleInput.value).toBe("");

    fireEvent.change(screen.getByTestId("composer-input"), { target: { value: "Invio normale" } });
    fireEvent.click(screen.getByTestId("composer-send"));
    await waitFor(() => expect(send).toHaveBeenCalledOnce());

    expect(send.mock.calls[0]).toHaveLength(4);
  });
});

describe("SessionView scheduled messages display and actions (RF-65)", () => {
  it("renders scheduled message as Programmato per <local time> with actions modify, send now, cancel", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    const onSchedule = vi.fn().mockResolvedValue(true);
    const onDelivery = vi.fn().mockResolvedValue(true);

    const scheduledReq = {
      id: 42,
      sender_id: "human",
      recipient_id: "claude",
      recipient_session_id: "s1",
      question_type: "message",
      prompt: "Controlla il server stanotte",
      status: "APPROVED",
      scheduled_at: "2026-10-25T20:00:00Z",
      created_at: "2026-10-22T00:00:00Z",
    };

    render(
      <SessionView
        sessionId="s1"
        session={session}
        requests={[scheduledReq]}
        sessions={[session]}
        onDelivery={onDelivery}
        onSchedule={onSchedule}
        onSend={vi.fn()}
      />
    );

    await waitFor(() => expect(screen.getByTestId("outgoing-42")).toBeInTheDocument());
    const outgoing = screen.getByTestId("outgoing-42");
    expect(outgoing).toHaveTextContent(/Programmato per|Scheduled for/);

    // Actions: Send now (Invia subito), Modify (Modifica), Cancel (Annulla)
    const sendNowBtn = screen.getByTestId("send-now-42");
    expect(sendNowBtn).toBeInTheDocument();
    fireEvent.click(sendNowBtn);
    expect(onSchedule).toHaveBeenCalledWith(42, null);

    const cancelBtn = screen.getByTestId("cancel-42");
    expect(cancelBtn).toBeInTheDocument();
    fireEvent.click(cancelBtn);
    expect(onDelivery).toHaveBeenCalledWith(42, "cancel");

    const modifyBtn = screen.getByTestId("modify-42");
    expect(modifyBtn).toBeInTheDocument();
  });

  it("modifies scheduled message: withdraws message and preloads scheduled_at in composer", async () => {
    mockFetch([[transcriptOf("s1"), latest]]);
    const onDelivery = vi.fn().mockResolvedValue(true);

    const scheduledReq = {
      id: 43,
      sender_id: "human",
      recipient_id: "claude",
      recipient_session_id: "s1",
      question_type: "message",
      prompt: "Aggiorna il database",
      status: "APPROVED",
      scheduled_at: "2026-10-25T20:00:00Z",
      created_at: "2026-10-22T00:00:00Z",
    };

    render(
      <SessionView
        sessionId="s1"
        session={session}
        requests={[scheduledReq]}
        sessions={[session]}
        onDelivery={onDelivery}
        onSchedule={vi.fn()}
        onSend={vi.fn()}
      />
    );

    await waitFor(() => expect(screen.getByTestId("outgoing-43")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("edit-43"));
    expect(onDelivery).toHaveBeenCalledWith(43, "cancel");

    await waitFor(() => expect(screen.getByTestId("composer-input")).toHaveValue("Aggiorna il database"));
    const scheduleInput = screen.getByTestId("composer-schedule-input");
    expect(scheduleInput.value).toBeTruthy();
  });
});
