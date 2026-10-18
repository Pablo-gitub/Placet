import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import AutomationBar from "../AutomationBar";
import { json, mockFetch } from "./testUtils";

const active = { paused: false, max_hops: 3, max_per_hour: 20, automatic_relations: 2 };

describe("AutomationBar", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("stays hidden without automatic relations", async () => {
    const fetch = mockFetch([["/api/automation", { ...active, automatic_relations: 0 }]]);
    vi.stubGlobal("fetch", fetch);
    render(<AutomationBar />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("automation-bar")).toBeNull();
  });

  it("stops all, resumes and saves the limits", async () => {
    const fetch = mockFetch([
      ["/api/automation/stop", { ...active, paused: true, held: [4] }],
      ["/api/automation/resume", active],
      ["/api/automation", (url, o) => (o.method === "PUT" ? json({ ...active, ...JSON.parse(o.body) }) : active)],
    ]);
    vi.stubGlobal("fetch", fetch);
    render(<AutomationBar />);
    fireEvent.click(await screen.findByTestId("automation-stop"));
    fireEvent.click(await screen.findByTestId("automation-resume"));
    await screen.findByTestId("automation-stop");
    fireEvent.click(screen.getByRole("button", { name: /limit/i }));
    fireEvent.change(screen.getByTestId("automation-hops"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("automation-save"));
    await waitFor(() => {
      const put = fetch.mock.calls.find(([u, o]) => u === "/api/automation" && o?.method === "PUT");
      expect(JSON.parse(put[1].body)).toEqual({ max_hops: 5, max_per_hour: 20 });
    });
  });
});
