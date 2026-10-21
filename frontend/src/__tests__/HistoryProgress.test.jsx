import React from "react";
import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import History from "../History";

it("shows the latest progress of a running request (RFC-16)", () => {
  const req = {
    id: 7, sender_id: "claude", recipient_id: "gemini", question_type: "prompt_review", prompt: "implementa",
    options: [], status: "EXECUTING", created_at: "2026-10-21T10:00:00Z", progress_text: "Parte 1 di 4 fatta",
  };
  render(<History requests={[req]} sessions={[]} />);
  expect(screen.getByTestId("history-progress-7")).toHaveTextContent("Parte 1 di 4 fatta");
});
