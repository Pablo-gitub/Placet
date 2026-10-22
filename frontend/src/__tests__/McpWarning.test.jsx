import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import Home from "../Home";
import SessionView from "../SessionView";
import i18n from "../i18n";
import { mockFetch, transcriptOf } from "./testUtils";
import { mcpWarning } from "../aspects";

const base = {
  id: "s1", agent_id: "claude", provider: "claude-code", entrypoint: "cli", cwd: "/p/lab", title: "Lab",
  state: "idle", reachable: true, pinned: true, activity_at: "2026-10-16T10:00:00Z",
  started_at: "2026-10-16T09:00:00Z", last_seen_at: "2026-10-16T10:00:00Z",
};

describe("the missing Placet MCP warning", () => {
  it("shows for missing only, never from how a turn ended", () => {
    expect(mcpWarning({ ...base, mcp_state: "missing" })).toBe("missing");
    // "suspect" is gone: a healthy session never needs an MCP call, so a leftover value says nothing.
    for (const mcp_state of ["suspect", "configured", "unknown", null, undefined]) {
      expect(mcpWarning({ ...base, mcp_state })).toBeNull();
    }
  });

  it("considers both: a use since this start hides the warning, a use before it does not", () => {
    const at = (minutes) => new Date(Date.UTC(2026, 9, 22, 10, minutes)).toISOString();
    for (const mcp_state of ["missing"]) {
      expect(mcpWarning({ ...base, mcp_state, mcp_checked_at: at(5), mcp_seen_at: at(2) })).toBe(mcp_state);   // used before
      expect(mcpWarning({ ...base, mcp_state, mcp_checked_at: at(5), mcp_seen_at: at(9) })).toBeNull();        // used since
      expect(mcpWarning({ ...base, mcp_state, mcp_checked_at: at(5), mcp_seen_at: at(5) })).toBeNull();        // same moment
      expect(mcpWarning({ ...base, mcp_state, mcp_checked_at: at(5), mcp_seen_at: null })).toBe(mcp_state);    // never
      expect(mcpWarning({ ...base, mcp_state, mcp_checked_at: null, mcp_seen_at: at(1) })).toBeNull();         // no check time: older record
    }
  });

  it("appears in the Home row, with a hint to the installation guide", () => {
    render(<Home requests={[]} sessions={[{ ...base, mcp_state: "missing" }, { ...base, id: "s2", mcp_state: "configured" }]}
      usage={null} onDecide={vi.fn()} onOpenSession={vi.fn()} onTogglePin={vi.fn()} />);
    const badge = screen.getByTestId("session-mcp-s1");
    expect(badge).toHaveAttribute("title", expect.stringContaining(i18n.t("mcp.hintMissing")));
    expect(screen.queryByTestId("session-mcp-s2")).toBeNull();
  });

  it("appears in the session header, and the composer still works", async () => {
    mockFetch([[transcriptOf("s1"), { session_id: "s1", provider: "claude-code", before: 0, end: 0, messages: [] }]]);
    render(<SessionView sessionId="s1" session={{ ...base, mcp_state: "missing" }} requests={[]}
      sessions={[base]} onBack={vi.fn()} onDecide={vi.fn()} onSend={vi.fn()} />);
    const warning = screen.getByTestId("session-view-mcp");
    expect(warning).toHaveTextContent(i18n.t("mcp.warning"));
    expect(warning.querySelector("a")).toHaveAttribute("href", expect.stringContaining("docs/installation.md#"));
    expect(screen.queryByTestId("composer-input") || screen.queryByRole("textbox")).not.toBeNull();
  });

  it("has both languages", () => {
    for (const lng of ["en", "it"]) {
      for (const key of ["mcp.warning", "mcp.hintMissing", "mcp.guide"]) {
        expect(i18n.getFixedT(lng)(key)).not.toBe(key);
      }
    }
  });
});

describe("the warning does not overstate what Placet knows", () => {
  it("says what was not found, carefully, in both languages, and has no hint left for an inferred state", () => {
    for (const lng of ["en", "it"]) {
      const t = i18n.getFixedT(lng);
      const missing = t("mcp.hintMissing");
      expect(missing).not.toBe("mcp.hintMissing");
      for (const text of [t("mcp.warning"), missing]) {
        expect(text).not.toMatch(/cannot|can't|non può|impossibile/i);
      }
      expect(t("mcp.hintSuspect")).toBe("mcp.hintSuspect");          // the string was removed with the heuristic
    }
  });

  it("shows the hint for a configuration that was not found, and nothing for a leftover suspect", () => {
    const { rerender } = render(<Home requests={[]} sessions={[{ ...base, mcp_state: "suspect" }]}
      usage={null} onDecide={vi.fn()} onOpenSession={vi.fn()} onTogglePin={vi.fn()} />);
    expect(screen.queryByTestId("session-mcp-s1")).toBeNull();
    rerender(<Home requests={[]} sessions={[{ ...base, mcp_state: "missing" }]}
      usage={null} onDecide={vi.fn()} onOpenSession={vi.fn()} onTogglePin={vi.fn()} />);
    expect(screen.getByTestId("session-mcp-s1")).toHaveAttribute("title", expect.stringContaining(i18n.t("mcp.hintMissing")));
  });
});
