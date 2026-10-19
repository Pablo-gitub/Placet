import React from "react";
import { render, screen, within } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import AddSessionsDialog from "../AddSessionsDialog";

const now = Date.now();
const at = (minutes) => new Date(now - minutes * 60000).toISOString();
const session = (id, cwd, minutes, extra = {}) => ({
  id, agent_id: "claude", provider: "claude-code", cwd, title: `Chat ${id}`,
  started_at: at(minutes + 5), last_seen_at: at(minutes), activity_at: at(minutes), reachable: true, ...extra,
});

const sessions = [
  session("a1", "/home/u/Projects/placet", 1),
  session("a2", "/home/u/Projects/placet", 30),
  session("b1", "/home/u/Projects/sito", 10, { pinned: true }),
  session("c1", "/home/u/Projects/archivio", 600),
  session("d1", null, 900),
];

function renderDialog() {
  return render(<AddSessionsDialog sessions={sessions} onToggle={vi.fn()} onOpen={vi.fn()} onClose={vi.fn()} />);
}

it("groups a provider's sessions by working folder, most recent folder first", () => {
  renderDialog();
  const groups = screen.getAllByTestId(/^pick-folder-(?!count)/);
  expect(groups.map((g) => g.dataset.testid)).toEqual([
    "pick-folder-/home/u/Projects/placet", "pick-folder-/home/u/Projects/sito",
    "pick-folder-/home/u/Projects/archivio", "pick-folder-",
  ]);
  const placet = groups[0];
  expect(within(placet).getByText("placet")).toBeInTheDocument();
  expect(within(placet).getByTestId("pick-folder-count")).toHaveTextContent("2");
  expect(within(placet).getByTestId("pick-a1")).toBeInTheDocument();
  expect(within(placet).getByTestId("pick-a2")).toBeInTheDocument();
});

it("opens the most recent folder and the folders with followed sessions, folds the others", () => {
  renderDialog();
  expect(screen.getByTestId("pick-folder-/home/u/Projects/placet")).toHaveAttribute("open");
  expect(screen.getByTestId("pick-folder-/home/u/Projects/sito")).toHaveAttribute("open");
  expect(screen.getByTestId("pick-folder-/home/u/Projects/archivio")).not.toHaveAttribute("open");
});

it("tells apart folders with the same name", () => {
  render(<AddSessionsDialog onToggle={vi.fn()} onOpen={vi.fn()} onClose={vi.fn()} sessions={[
    session("x", "/home/u/work/app", 1), session("y", "/home/u/play/app", 2)]} />);
  expect(screen.getByText("work/app")).toBeInTheDocument();
  expect(screen.getByText("play/app")).toBeInTheDocument();
});

it("scrolls inside the dialog body", () => {
  renderDialog();
  expect(screen.getByTestId("add-sessions-body")).toHaveClass("dialog-body", "dialog-scroll");
});
