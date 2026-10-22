import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import i18n from "../i18n";
import { ProjectDetailView } from "../Projects";
import { mockFetch } from "./testUtils";

const project = {
  id: 1, name: "Optees Finance", description: "Un progetto di ai applicativa che utilizza optees e altri strumenti per creare",
  relation_rule: "", max_auto_hops: 3, max_auto_per_hour: 10, created_at: "2026-10-19T10:00:00Z",
  instructions: "", effective_rule: "", effective_max_hops: 3, effective_max_per_hour: 20,
  nodes: [{ id: 1, name: "Optees_finance_project_owner", session_id: null, instructions: "" }],
  relations: [
    { id: 11, from_node: 1, to_node: 2, from_name: "Optees_finance_project_owner", to_name: "Finance_gpt_developer",
      type_name: "Developer", approval: "automatic", reply_approval: "automatic", shared_with: [] },
    { id: 12, from_node: 1, to_node: 3, from_name: "Optees_finance_project_owner", to_name: "Optees_finance_claude_reviewer",
      type_name: "Reviewer", approval: "automatic", reply_approval: "automatic", shared_with: ["Altro progetto"] },
  ],
};

function serveProject() {
  vi.stubGlobal("fetch", mockFetch([
    ["/api/projects/1", project], ["/api/nodes", []], ["/api/relations", []], ["/api/relation-types", []], ["/api/sessions", []],
  ]));
}

describe("project detail layout on a narrow pane", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lays each relation out as the full names first, then the controls on a row of their own", async () => {
    serveProject();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const row = await screen.findByTestId("project-relation-12");
    const [route, actions] = row.children;
    expect(route).toHaveClass("relation-route");
    expect(actions).toHaveClass("project-relation-actions");
    // The names are whole in the text, never cut: the stylesheet wraps them.
    expect(route).toHaveTextContent("Optees_finance_project_owner");
    expect(route).toHaveTextContent("Optees_finance_claude_reviewer");
    expect(route).toHaveTextContent("(Reviewer)");
    // Both selects and the delete button belong to the controls row, not to the names.
    expect(within(actions).getAllByRole("combobox")).toHaveLength(2);
    expect(within(actions).getByTestId("remove-relation-12")).toBeInTheDocument();
    expect(within(route).queryByRole("combobox")).toBeNull();
  });

  it("sits in a container of its own, so the diagram goes beside the project only if the pane is wide", async () => {
    serveProject();
    render(<ProjectDetailView projectId={1} onBack={vi.fn()} />);
    const view = await screen.findByTestId("project-detail-view");
    const container = view.querySelector(".project-detail-container");
    expect(container).not.toBeNull();
    expect(container.querySelector(".project-detail-layout")).not.toBeNull();
    expect(container.querySelector(".project-graph-panel")).not.toBeNull();
    expect(container.querySelector(".project-detail-nav")).not.toBeNull();
  });
});

describe("collapsible sidebar on a wide screen", () => {
  const original = window.matchMedia;
  const screenWide = (wide) => {
    window.matchMedia = (query) => ({ matches: wide, media: query, addEventListener: () => {}, removeEventListener: () => {} });
  };
  beforeEach(() => {
    window.location.hash = "";
    localStorage.clear();
    screenWide(true);
    global.fetch = mockFetch([
      ["/api/requests", []], ["/api/sessions", []], ["/api/usage", []], ["/api/projects", []], ["/api/automation", { paused: false }],
      ["/api/settings/language", { language: "it" }],
    ]);
  });
  afterEach(() => { window.matchMedia = original; vi.restoreAllMocks(); });

  it("has a toggle button, open by default, with an accessible name and state", async () => {
    render(<App />);
    const toggle = await screen.findByTestId("sidebar-toggle");
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAttribute("aria-controls", "sidebar");
    expect(toggle).toHaveAccessibleName(i18n.t("app.hideSidebar"));
    expect(screen.getByTestId("sidebar")).not.toHaveAttribute("hidden");
  });

  it("hides the column so the right pane takes the full width, and remembers it", async () => {
    const { unmount } = render(<App />);
    fireEvent.click(await screen.findByTestId("sidebar-toggle"));
    const toggle = screen.getByTestId("sidebar-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAccessibleName(i18n.t("app.showSidebar"));
    expect(screen.getByTestId("sidebar")).toHaveAttribute("hidden");
    expect(screen.getByTestId("pane").parentElement).toHaveClass("sidebar-collapsed");
    expect(localStorage.getItem("placet.sidebarCollapsed")).toBe("1");
    unmount();
    render(<App />);                                                   // another visit on this device
    expect((await screen.findByTestId("sidebar-toggle"))).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("sidebar")).toHaveAttribute("hidden");
    fireEvent.click(screen.getByTestId("sidebar-toggle"));             // and back open
    expect(screen.getByTestId("sidebar")).not.toHaveAttribute("hidden");
    expect(localStorage.getItem("placet.sidebarCollapsed")).toBe("0");
  });

  it("works when the browser refuses to store anything", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<App />);
    fireEvent.click(await screen.findByTestId("sidebar-toggle"));
    expect(screen.getByTestId("sidebar")).toHaveAttribute("hidden");   // still works for this visit
  });

  it("keeps the sidebar mounted while hidden and is absent on the phone", async () => {
    const { unmount } = render(<App />);
    fireEvent.click(await screen.findByTestId("sidebar-toggle"));
    expect(within(screen.getByTestId("sidebar")).getByTestId("home-section")).toBeInTheDocument();
    unmount();
    screenWide(false);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("nav-tabs")).toBeInTheDocument());
    expect(screen.queryByTestId("sidebar-toggle")).toBeNull();
  });

  it("has both languages", () => {
    for (const lng of ["en", "it"]) {
      for (const key of ["app.hideSidebar", "app.showSidebar"]) expect(i18n.getFixedT(lng)(key)).not.toBe(key);
    }
  });
});
