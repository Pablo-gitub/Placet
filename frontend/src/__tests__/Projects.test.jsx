import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { ProjectsView, ProjectDetailView } from "../Projects";
import App from "../App";
import { json, mockFetch } from "./testUtils";

const bodies = (fetch, method, url) =>
  fetch.mock.calls.filter(([u, o]) => u === url && o?.method === method).map(([, o]) => o?.body ? JSON.parse(o.body) : null);

describe("Projects views (RFC-09, RF-44)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = "";
  });

  const sampleProjects = [
    {
      id: 1,
      name: "Placet Core",
      description: "Core project",
      relation_rule: "Test rule",
      max_auto_hops: 3,
      max_auto_per_hour: 10,
      created_at: "2026-10-19T10:00:00Z",
      nodes_count: 2,
      relations_count: 1,
      automatic_relations_count: 1,
    },
  ];

  const sampleTypes = [
    { id: 10, name: "review", description: "Code review" },
  ];

  it("lists projects with counts and handles project creation, editing and deletion", async () => {
    const fetch = mockFetch([
      ["/api/projects", (url, o) => {
        if (o.method === "POST") {
          return json({ id: 2, ...JSON.parse(o.body), created_at: "2026-10-19T11:00:00Z" }, 201);
        }
        return sampleProjects;
      }],
      ["/api/projects/1", (url, o) => {
        if (o.method === "PATCH") return json({ ...sampleProjects[0], ...JSON.parse(o.body) });
        if (o.method === "DELETE") return json(null, 204);
        return sampleProjects[0];
      }],
      ["/api/relation-types", sampleTypes],
    ]);
    vi.stubGlobal("fetch", fetch);
    window.confirm = vi.fn(() => true);

    const onOpen = vi.fn();
    render(<ProjectsView onOpenProject={onOpen} />);

    // 1. Projects listed
    expect(await screen.findByText("Placet Core")).toBeInTheDocument();
    expect(screen.getByText("Core project")).toBeInTheDocument();
    expect(screen.getByTestId("project-nodes-count-1")).toHaveTextContent("2");
    expect(screen.getByTestId("project-relations-count-1")).toHaveTextContent("1");

    // Click project to open detail
    fireEvent.click(screen.getByTestId("open-project-1"));
    expect(onOpen).toHaveBeenCalledWith(1);

    // 2. Create project
    fireEvent.click(screen.getByTestId("new-project-btn"));
    const modal = screen.getByTestId("project-form-dialog");
    fireEvent.change(within(modal).getByTestId("project-name-input"), { target: { value: "Frontend UI" } });
    fireEvent.change(within(modal).getByTestId("project-desc-input"), { target: { value: "UI work" } });
    fireEvent.change(within(modal).getByTestId("project-rule-input"), { target: { value: "Run lint" } });
    fireEvent.change(within(modal).getByTestId("project-hops-input"), { target: { value: "4" } });
    fireEvent.change(within(modal).getByTestId("project-rate-input"), { target: { value: "12" } });
    fireEvent.click(within(modal).getByTestId("project-save-btn"));

    await waitFor(() => {
      expect(bodies(fetch, "POST", "/api/projects")).toEqual([{
        name: "Frontend UI",
        description: "UI work",
        relation_rule: "Run lint",
        max_auto_hops: 4,
        max_auto_per_hour: 12,
      }]);
    });

    // 3. Edit project
    fireEvent.click(screen.getByTestId("edit-project-1"));
    const editModal = screen.getByTestId("project-form-dialog");
    fireEvent.change(within(editModal).getByTestId("project-desc-input"), { target: { value: "Updated core description" } });
    fireEvent.click(within(editModal).getByTestId("project-save-btn"));

    await waitFor(() => {
      expect(bodies(fetch, "PATCH", "/api/projects/1")).toEqual([{
        name: "Placet Core",
        description: "Updated core description",
        relation_rule: "Test rule",
        max_auto_hops: 3,
        max_auto_per_hour: 10,
      }]);
    });

    // 3b. Edit project and clear overrides to inherit
    fireEvent.click(screen.getByTestId("edit-project-1"));
    const clearModal = screen.getByTestId("project-form-dialog");
    fireEvent.change(within(clearModal).getByTestId("project-rule-input"), { target: { value: "" } });
    fireEvent.change(within(clearModal).getByTestId("project-hops-input"), { target: { value: "" } });
    fireEvent.change(within(clearModal).getByTestId("project-rate-input"), { target: { value: "" } });
    fireEvent.click(within(clearModal).getByTestId("project-save-btn"));

    await waitFor(() => {
      expect(bodies(fetch, "PATCH", "/api/projects/1")).toContainEqual({
        name: "Placet Core",
        description: "Core project",
        relation_rule: null,
        max_auto_hops: null,
        max_auto_per_hour: null,
      });
    });

    // 4. Delete project
    fireEvent.click(screen.getByTestId("delete-project-1"));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => {
      expect(fetch.mock.calls.some(([u, o]) => u === "/api/projects/1" && o?.method === "DELETE")).toBe(true);
    });
  });

  it("manages relation types in relation types section", async () => {
    const fetch = mockFetch([
      ["/api/projects", []],
      ["/api/relation-types", (url, o) => {
        if (o.method === "POST") return json({ id: 11, ...JSON.parse(o.body) }, 201);
        return sampleTypes;
      }],
      ["/api/relation-types/10", (url, o) => {
        if (o.method === "PATCH") return json({ id: 10, ...JSON.parse(o.body) });
        if (o.method === "DELETE") return json(null, 204);
        return sampleTypes[0];
      }],
    ]);
    vi.stubGlobal("fetch", fetch);
    window.confirm = vi.fn(() => true);

    render(<ProjectsView onOpenProject={() => {}} />);

    // Lists types
    expect(await screen.findByText("review")).toBeInTheDocument();
    expect(screen.getByText("Code review")).toBeInTheDocument();

    // Create type
    fireEvent.click(screen.getByTestId("new-type-btn"));
    const typeDialog = screen.getByTestId("relation-type-dialog");
    fireEvent.change(within(typeDialog).getByTestId("relation-type-name-input"), { target: { value: "audit" } });
    fireEvent.change(within(typeDialog).getByTestId("relation-type-desc-input"), { target: { value: "Security audit" } });
    fireEvent.click(within(typeDialog).getByTestId("relation-type-save-btn"));

    await waitFor(() => {
      expect(bodies(fetch, "POST", "/api/relation-types")).toEqual([{
        name: "audit",
        description: "Security audit",
      }]);
    });

    // Edit type
    fireEvent.click(screen.getByTestId("edit-type-10"));
    const editDialog = screen.getByTestId("relation-type-dialog");
    fireEvent.change(within(editDialog).getByTestId("relation-type-desc-input"), { target: { value: "Updated review desc" } });
    fireEvent.click(within(editDialog).getByTestId("relation-type-save-btn"));

    await waitFor(() => {
      expect(bodies(fetch, "PATCH", "/api/relation-types/10")).toEqual([{
        name: "review",
        description: "Updated review desc",
      }]);
    });

    // Delete type
    fireEvent.click(screen.getByTestId("delete-type-10"));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => {
      expect(fetch.mock.calls.some(([u, o]) => u === "/api/relation-types/10" && o?.method === "DELETE")).toBe(true);
    });
  });

  it("renders project detail, manages nodes and relations with shared_with warning and 409 handling", async () => {
    const projectDetail = {
      id: 1,
      name: "Placet Core",
      description: "Core project",
      relation_rule: "Core rule",
      max_auto_hops: 3,
      max_auto_per_hour: 10,
      created_at: "2026-10-19T10:00:00Z",
      effective_rule: "Global safeguard\n\n[Placet Core]\nCore rule",
      effective_max_hops: 3,
      effective_max_per_hour: 10,
      nodes: [
        { id: 101, name: "planner", session_id: "cl" },
      ],
      relations: [
        {
          id: 201,
          from_node: 101,
          to_node: 102,
          from_name: "planner",
          to_name: "builder",
          type_id: 10,
          type_name: "review",
          type_description: "Code review",
          approval: "automatic",
          shared_with: ["Other Project"],
        },
      ],
    };

    const allNodes = [
      { id: 101, name: "planner" },
      { id: 102, name: "builder" },
      { id: 103, name: "tester" },
    ];

    const allRelations = [
      { id: 201, from_node: 101, to_node: 102, type_id: 10, from_name: "planner", to_name: "builder", type_name: "review", approval: "automatic" },
      { id: 202, from_node: 102, to_node: 103, type_id: 10, from_name: "builder", to_name: "tester", type_name: "review", approval: "manual" },
    ];

    const fetch = mockFetch([
      ["/api/projects/1", projectDetail],
      ["/api/nodes", allNodes],
      ["/api/relations", (url, o) => {
        if (o.method === "POST") {
          const body = JSON.parse(o.body);
          if (body.from_node === 101 && body.to_node === 102) {
            // Already exists -> 409
            return json({ code: "relation.exists" }, 409);
          }
          return json({ id: 203, ...body }, 201);
        }
        return allRelations;
      }],
      ["/api/relation-types", sampleTypes],
      ["/api/projects/1/nodes/103", () => json({ ok: true, removed_relations: [] })],
      ["/api/projects/1/nodes/101", () => json({ ok: true, removed_relations: [201] })],
      ["/api/projects/1/relations/202", () => json({ ok: true, shared_with: [] })],
      ["/api/projects/1/relations/201", () => json({ ok: true, shared_with: ["Other Project"] })],
      ["/api/projects/1/relations/203", () => json({ ok: true, shared_with: [] })],
    ]);
    vi.stubGlobal("fetch", fetch);
    window.confirm = vi.fn(() => true);

    const onBack = vi.fn();
    render(<ProjectDetailView projectId={1} onBack={onBack} />);

    // 1. Detail display
    expect(await screen.findByText("Placet Core")).toBeInTheDocument();
    expect(screen.getByText("planner")).toBeInTheDocument();
    expect(screen.getByTestId("relation-badge-shared-201")).toHaveTextContent("Other Project");

    // 2. Add existing node
    fireEvent.change(screen.getByTestId("add-node-select"), { target: { value: "103" } });
    fireEvent.click(screen.getByTestId("add-node-btn"));
    await waitFor(() => {
      expect(fetch.mock.calls.some(([u, o]) => u === "/api/projects/1/nodes/103" && o?.method === "PUT")).toBe(true);
    });

    // 3. Remove node from project
    fireEvent.click(screen.getByTestId("remove-node-101"));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => {
      expect(fetch.mock.calls.some(([u, o]) => u === "/api/projects/1/nodes/101" && o?.method === "DELETE")).toBe(true);
    });

    // 4. Associate existing relation
    fireEvent.change(screen.getByTestId("add-relation-select"), { target: { value: "202" } });
    fireEvent.click(screen.getByTestId("add-relation-btn"));
    await waitFor(() => {
      expect(fetch.mock.calls.some(([u, o]) => u === "/api/projects/1/relations/202" && o?.method === "PUT")).toBe(true);
    });

    // 5. Remove relation from project
    fireEvent.click(screen.getByTestId("remove-relation-201"));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => {
      expect(fetch.mock.calls.some(([u, o]) => u === "/api/projects/1/relations/201" && o?.method === "DELETE")).toBe(true);
    });

    // 6. Create new relation: when 409 relation.exists, confirm dialog asks to associate it
    fireEvent.click(screen.getByTestId("open-new-relation-btn"));
    const relModal = screen.getByTestId("relation-dialog");
    fireEvent.change(within(relModal).getByTestId("new-rel-from-select"), { target: { value: "101" } });
    fireEvent.change(within(relModal).getByTestId("new-rel-to-select"), { target: { value: "102" } });
    fireEvent.change(within(relModal).getByTestId("new-rel-type-select"), { target: { value: "10" } });
    fireEvent.click(within(relModal).getByTestId("new-rel-save-btn"));

    // Since 101 -> 102 exists, prompt appears and user confirms associating existing relation 201
    await waitFor(() => {
      expect(fetch.mock.calls.some(([u, o]) => u === "/api/projects/1/relations/201" && o?.method === "PUT")).toBe(true);
    });

    // 7. Back button
    fireEvent.click(screen.getByTestId("project-detail-back"));
    expect(onBack).toHaveBeenCalled();
  });

  it("integrates with App navigation via tab-projects and hash routes", async () => {
    const fetch = mockFetch([
      ["/api/requests", []],
      ["/api/sessions?include_ended=true", []],
      ["/api/projects", sampleProjects],
      ["/api/projects/1", { ...sampleProjects[0], nodes: [], relations: [], effective_rule: "" }],
      ["/api/relation-types", []],
      ["/api/automation", { paused: false, automatic_relations: 0, max_hops: 3, max_per_hour: 20 }],
    ]);
    vi.stubGlobal("fetch", fetch);

    render(<App />);

    // Click tab-projects
    await waitFor(() => expect(screen.getByTestId("tab-projects")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("tab-projects"));

    expect(window.location.hash).toBe("#/progetti");
    expect(await screen.findByTestId("projects-view")).toBeInTheDocument();
  });
});
