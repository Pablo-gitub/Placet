import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import App from "../App";

// Mock EventSource globally
class MockEventSource {
  constructor(url) {
    this.url = url;
    setTimeout(() => {
      if (this.onopen) this.onopen();
    }, 0);
  }
  addEventListener(event, callback) {
    this.callback = callback;
  }
  close() {}
}
global.EventSource = MockEventSource;

describe("Placet Frontend PWA App", () => {
  const mockRequests = [
    {
      id: 1,
      sender_id: "claude",
      recipient_id: "gemini",
      question_type: "prompt_review",
      prompt: "Original prompt proposal",
      context: "Refactoring test context",
      options: [],
      status: "PENDING",
      created_at: new Date().toISOString(),
    },
    {
      id: 2,
      sender_id: "claude",
      recipient_id: "human",
      question_type: "single_choice",
      prompt: "Which choice?",
      options: ["Choice A", "Choice B"],
      status: "COMPLETED",
      response: {
        responder_id: "human",
        content: "Choice A",
      },
      created_at: new Date().toISOString(),
    },
  ];

  const mockAgents = {
    agents: {
      claude: { name: "Claude Code", tmux_session: "agent-claude", allowed_targets: ["gemini"], default_timeout_seconds: 180 },
      gemini: { name: "Gemini CLI", tmux_session: "agent-gemini", allowed_targets: ["claude"], default_timeout_seconds: 180 },
    },
    rules: {
      "claude -> gemini": { require_approval: true },
    },
  };

  const decisionBody = (id) => {
    const call = global.fetch.mock.calls.find(([url]) => url === `/api/requests/${id}/decision`);
    return call ? JSON.parse(call[1].body) : undefined;
  };

  beforeEach(() => {
    window.location.hash = "";
    vi.restoreAllMocks();
    global.fetch = vi.fn((url, options) => {
      if (url === "/api/requests" && (!options || options.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockRequests),
        });
      }
      if (url === "/api/agents") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockAgents),
        });
      }
      if (url.includes("/decision") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ id: 1, status: "APPROVED" }),
        });
      }
      return Promise.reject(new Error("Unknown route " + url));
    });
  });

  it("renders header, live status, and inbox with pending request", async () => {
    render(<App />);

    expect(screen.getByText("Placet")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("request-card-1")).toBeInTheDocument();
    });

    expect(screen.getByText("claude")).toBeInTheDocument();
    expect(screen.getByText("gemini")).toBeInTheDocument();
    expect(screen.getByText("Refactoring test context")).toBeInTheDocument();

    // Shown rendered first; the raw text appears with "Modifica".
    expect(screen.getByTestId("prompt-preview-1")).toHaveTextContent("Original prompt proposal");
    fireEvent.click(screen.getByTestId("toggle-edit-1"));
    expect(screen.getByTestId("prompt-textarea-1").value).toBe("Original prompt proposal");
  });

  it("allows editing the prompt before approving and sends edited text", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("toggle-edit-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("toggle-edit-1"));

    const textarea = screen.getByTestId("prompt-textarea-1");
    fireEvent.change(textarea, { target: { value: "Modified prompt by Paolo" } });
    expect(textarea.value).toBe("Modified prompt by Paolo");

    const approveBtn = screen.getByTestId("btn-approve-1");
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(decisionBody(1)).toEqual({
        status: "APPROVED",
        edited_prompt: "Modified prompt by Paolo",
        selected_options: [],
      });
    });
  });

  it("switches tabs between Inbox, Agenti, and Storico correctly", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId("request-card-1")).toBeInTheDocument();
    });

    // Switch to Agenti tab
    const agentsTab = screen.getByTestId("tab-agents");
    fireEvent.click(agentsTab);

    await waitFor(() => {
      expect(screen.getByTestId("agents-section")).toBeInTheDocument();
      expect(screen.getByText("tmux: agent-claude")).toBeInTheDocument();
      expect(screen.getByText("tmux: agent-gemini")).toBeInTheDocument();
    });

    // Switch to Storico tab
    const historyTab = screen.getByTestId("tab-history");
    fireEvent.click(historyTab);

    await waitFor(() => {
      expect(screen.getByTestId("history-section")).toBeInTheDocument();
      expect(screen.getByTestId("history-card-2")).toBeInTheDocument();
      expect(screen.getByText("Which choice?")).toBeInTheDocument();
      expect(screen.getByText("Choice A")).toBeInTheDocument();
    });
  });
});
