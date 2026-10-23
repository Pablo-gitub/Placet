import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import i18n from "../i18n";
import RelationGraph from "../RelationGraph";
import { formatWhen } from "../i18n/formatters";

// The real nodes and relations of the Placet project (October 19, 2026).
const nodes = [
  { id: 1, name: "Placet_claude_FS", provider: "claude-code" },
  { id: 2, name: "Placet_gemini_dev", provider: "antigravity" },
  { id: 3, name: "Placet_gpt_reviewer", provider: "codex" },
];
const relations = [
  { id: 1, from_node: 1, to_node: 2, from_name: "Placet_claude_FS", to_name: "Placet_gemini_dev", type_name: "Developer", approval: "automatic" },
  { id: 3, from_node: 1, to_node: 3, from_name: "Placet_claude_FS", to_name: "Placet_gpt_reviewer", type_name: "Second_reviewer", approval: "manual" },
];

it("draws an arrow on every relation, automatic and manual", () => {
  const { container } = render(<RelationGraph nodes={nodes} relations={relations} />);
  const markers = new Set([...container.querySelectorAll("marker")].map((m) => `url(#${m.id})`));
  const edges = [...container.querySelectorAll("path.rg-edge")];
  expect(edges).toHaveLength(2);
  for (const edge of edges) expect(markers).toContain(edge.getAttribute("marker-end"));
});

it("drops the prefix the node names share, so labels stay distinct", () => {
  render(<RelationGraph nodes={nodes} relations={relations} />);
  for (const label of ["claude_FS", "gemini_dev", "gpt_reviewer"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});

it("navigates to session with project context on node click", () => {
  const onOpen = vi.fn();
  const nodesWithSessions = [
    { id: 1, name: "node-1", provider: "claude-code", session_id: "s1" },
  ];
  render(<RelationGraph nodes={nodesWithSessions} relations={[]} projectId={5} onOpenSession={onOpen} />);
  fireEvent.click(screen.getByTestId("graph-node-1"));
  expect(onOpen).toHaveBeenCalledWith("s1", 5);
});

it("supports keyboard navigation and accessibility attributes on nodes with session", () => {
  const onOpen = vi.fn();
  const testNodes = [
    { id: 1, name: "node-active", provider: "claude-code", session_id: "s1" },
    { id: 2, name: "node-idle", provider: "codex" },
  ];
  render(<RelationGraph nodes={testNodes} relations={[]} projectId={5} onOpenSession={onOpen} />);

  const activeNode = screen.getByTestId("graph-node-1");
  expect(activeNode).toHaveAttribute("role", "link");
  expect(activeNode).toHaveAttribute("tabindex", "0");
  expect(activeNode).toHaveAttribute("aria-label", "node-active");
  expect(activeNode).toHaveClass("rg-node-link");
  expect(activeNode.getAttribute("style") || "").not.toContain("cursor");

  // Enter key opens session
  fireEvent.keyDown(activeNode, { key: "Enter" });
  expect(onOpen).toHaveBeenCalledWith("s1", 5);

  // Space key opens session
  fireEvent.keyDown(activeNode, { key: " " });
  expect(onOpen).toHaveBeenCalledWith("s1", 5);

  const idleNode = screen.getByTestId("graph-node-2");
  expect(idleNode).not.toHaveAttribute("role");
  expect(idleNode).not.toHaveAttribute("tabindex");
  expect(idleNode).not.toHaveClass("rg-node-link");
});


describe("live activity (RF-44)", () => {
  const live = [
    { id: 1, name: "Placet_claude_FS", provider: "claude-code", session_id: "s1" },
    { id: 2, name: "Placet_gemini_dev", provider: "antigravity", session_id: "s2" },
    { id: 3, name: "Placet_gpt_reviewer", provider: "codex", session_id: "s3" },
  ];
  const req = (over) => ({ id: 9, relation_id: 1, status: "APPROVED", sender_id: "claude", recipient_id: "gemini", ...over });

  it("pulses the nodes whose session is working", () => {
    render(<RelationGraph nodes={live} relations={relations}
      sessions={[{ id: "s1", state: "busy", reachable: true }, { id: "s2", state: "idle", reachable: true }]} />);
    expect(screen.getByTestId("graph-node-1")).toHaveClass("rg-active");
    expect(screen.getByTestId("graph-node-2")).not.toHaveClass("rg-active");
  });

  it("moves a ball from sender to recipient while a prompt is being delivered", () => {
    render(<RelationGraph nodes={live} relations={relations} requests={[req({ status: "DELIVERING" })]} />);
    const ball = screen.getByTestId("graph-ball-1");
    expect(ball).toHaveClass("rg-ball-delivering");
    expect(ball.querySelector("animateMotion").getAttribute("keyPoints")).toBe("0;1");
  });

  it("keeps a queued prompt at the sender's end, still, with its own legend", () => {
    render(<RelationGraph nodes={live} relations={relations} requests={[req({ status: "APPROVED" })]} />);
    const ball = screen.getByTestId("graph-ball-1");
    expect(ball).toHaveClass("rg-ball-queued");
    expect(ball.querySelector("animateMotion")).toBeNull();
    expect(screen.getByTestId("graph-legend-queued")).toHaveTextContent(i18n.t("projects.graphQueued"));
    expect(screen.queryByTestId("graph-legend-waiting")).toBeNull();
  });

  it("does not show the queued legend once the delivery starts", () => {
    render(<RelationGraph nodes={live} relations={relations} requests={[req({ status: "DELIVERING" })]} />);
    expect(screen.queryByTestId("graph-legend-queued")).toBeNull();
  });

  it("highlights the edge while the recipient executes", () => {
    render(<RelationGraph nodes={live} relations={relations} requests={[req({ status: "EXECUTING" })]} />);
    expect(screen.getByTestId("graph-edge-1")).toHaveClass("rg-executing");
    expect(screen.queryByTestId("graph-ball-1")).toBeNull();
  });

  it("moves the ball back while an automatic reply returns", () => {
    render(<RelationGraph nodes={live} relations={relations}
      requests={[req({ status: "COMPLETED", response: { content: "ok" }, allow_auto_return: true, delivered_at: null })]} />);
    const ball = screen.getByTestId("graph-ball-1");
    expect(ball).toHaveClass("rg-ball-returning");
    expect(ball.querySelector("animateMotion").getAttribute("keyPoints")).toBe("1;0");
  });

  it("stops a yellow ball halfway while the chain waits for the user", () => {
    for (const r of [req({ status: "PENDING" }),
      req({ status: "COMPLETED", response: { content: "ok" }, allow_auto_return: false, delivered_at: null })]) {
      const { unmount } = render(<RelationGraph nodes={live} relations={relations} requests={[r]} />);
      const ball = screen.getByTestId("graph-ball-1");
      expect(ball).toHaveClass("rg-ball-waiting");
      expect(ball.querySelector("animateMotion")).toBeNull();
      expect(screen.getByTestId("graph-legend-waiting")).toBeInTheDocument();
      unmount();
    }
  });

  it("shows nothing for finished requests or other relations", () => {
    render(<RelationGraph nodes={live} relations={relations}
      requests={[req({ status: "COMPLETED", delivered_at: "2026-10-19T00:00:00Z" }), req({ id: 10, relation_id: 99, status: "DELIVERING" })]} />);
    expect(screen.queryByTestId("graph-ball-1")).toBeNull();
    expect(screen.getByTestId("graph-edge-1")).not.toHaveClass("rg-executing");
  });

  it("keeps the ball still when the user asks for reduced motion", () => {
    const original = window.matchMedia;
    window.matchMedia = (q) => ({ matches: q.includes("reduce"), addEventListener() {}, removeEventListener() {} });
    try {
      render(<RelationGraph nodes={live} relations={relations} requests={[req({ status: "DELIVERING" })]} />);
      expect(screen.getByTestId("graph-ball-1").querySelector("animateMotion")).toBeNull();
    } finally {
      window.matchMedia = original;
    }
  });
});

it("marks the relations whose replies need approval (RFC-16)", () => {
  const rels = [{ ...relations[0], reply_approval: "automatic" }, { ...relations[1], reply_approval: "manual" }];
  const { container } = render(<RelationGraph nodes={nodes} relations={rels} />);
  const [auto, manual] = container.querySelectorAll("path.rg-edge");
  expect(auto.getAttribute("marker-start")).toBeNull();
  expect(manual.getAttribute("marker-start")).toBe("url(#rg-reply-manual)");
  expect(screen.getByTestId("graph-legend-reply")).toBeInTheDocument();
});

describe("a request scheduled for later on the graph", () => {
  const later = new Date(Date.now() + 3 * 3600000).toISOString();
  const scheduled = { id: 5, relation_id: 1, status: "APPROVED", scheduled_at: later };

  it("is drawn with a clock and its delivery time, not as a stuck queued ball", () => {
    render(<RelationGraph nodes={nodes} relations={relations} requests={[scheduled]} />);
    const ball = screen.getByTestId("graph-ball-1");
    expect(ball).toHaveClass("rg-ball-scheduled");
    expect(ball).not.toHaveClass("rg-ball-queued");
    expect(ball.querySelector("animateMotion")).toBeNull();
    const clock = screen.getByTestId("graph-clock-1");
    expect(clock.querySelector("title").textContent).toBe(i18n.t("projects.graphScheduledAt", { time: formatWhen(later) }));
    expect(screen.getByTestId("graph-legend-scheduled")).toHaveTextContent(i18n.t("projects.graphScheduled"));
    expect(screen.queryByTestId("graph-legend-queued")).toBeNull();
  });

  it("does not add a clock to a plain queued ball", () => {
    render(<RelationGraph nodes={nodes} relations={relations} requests={[{ ...scheduled, scheduled_at: null }]} />);
    expect(screen.queryByTestId("graph-clock-1")).toBeNull();
    expect(screen.getByTestId("graph-ball-1")).toHaveClass("rg-ball-queued");
  });
});
