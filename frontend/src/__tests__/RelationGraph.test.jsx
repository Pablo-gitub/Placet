import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import RelationGraph from "../RelationGraph";

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

