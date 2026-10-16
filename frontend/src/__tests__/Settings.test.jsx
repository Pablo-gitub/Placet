import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import Settings from "../Settings";
import { json, mockFetch } from "./testUtils";

const folders = [
  { path: "/home/p/Projects/placet", name: "placet", providers: ["claude-code", "codex"], state: "on", last_used: 2 },
  { path: "/home/p/Projects/placet/frontend", name: "frontend", providers: [], state: "inherited", last_used: 1 },
  { path: "/home/p/Projects/social", name: "social", providers: ["antigravity"], state: "off", last_used: 0 },
];

describe("Settings: projects in Placet", () => {
  it("lists the folders with their state and switches one on", async () => {
    const fetchMock = mockFetch([
      [(url, o) => url === "/api/folders" && o.method === "PUT",
        (url, o) => ({ ...JSON.parse(o.body), name: "social", state: "on" })],
      ["/api/folders", { folders }],
    ]);
    render(<Settings />);
    await waitFor(() => expect(screen.getByTestId("folder-placet")).toBeInTheDocument());
    expect(screen.getByTestId("folder-placet")).toHaveTextContent("Claude · Codex · Attivo");
    expect(screen.getByTestId("folder-frontend")).toHaveTextContent("dalla cartella che lo contiene");
    expect(screen.getByTestId("folder-switch-frontend")).toBeChecked();
    expect(screen.getByTestId("folder-switch-social")).not.toBeChecked();
    fireEvent.click(screen.getByTestId("folder-switch-social"));
    await waitFor(() => expect(screen.getByTestId("folder-switch-social")).toBeChecked());
    const put = fetchMock.mock.calls.find(([, o]) => o?.method === "PUT");
    expect(JSON.parse(put[1].body)).toEqual({ path: "/home/p/Projects/social", active: true });
  });

  it("switching a project off turns off the folders it contained", async () => {
    mockFetch([
      [(url, o) => url === "/api/folders" && o.method === "PUT",
        { path: "/home/p/Projects/placet", name: "placet", state: "off" }],
      ["/api/folders", { folders }],
    ]);
    render(<Settings />);
    await waitFor(() => expect(screen.getByTestId("folder-switch-placet")).toBeChecked());
    fireEvent.click(screen.getByTestId("folder-switch-placet"));
    await waitFor(() => expect(screen.getByTestId("folder-switch-frontend")).not.toBeChecked());
  });

  it("activates a folder typed by hand and reports errors", async () => {
    mockFetch([
      [(url, o) => url === "/api/folders" && o.method === "PUT" && o.body.includes("/etc"),
        () => json({ detail: "Solo cartelle dentro la tua home, e non la home stessa." }, 400)],
      [(url, o) => url === "/api/folders" && o.method === "PUT",
        { path: "/home/p/Projects/nuovo", name: "nuovo", state: "on" }],
      ["/api/folders", { folders }],
    ]);
    render(<Settings />);
    await waitFor(() => expect(screen.getByTestId("new-folder")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("new-folder"), { target: { value: "/etc" } });
    fireEvent.click(screen.getByText("Attiva"));
    await waitFor(() => expect(screen.getByText(/Solo cartelle dentro la tua home/)).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("new-folder"), { target: { value: "/home/p/Projects/nuovo" } });
    fireEvent.click(screen.getByText("Attiva"));
    await waitFor(() => expect(screen.getByTestId("folder-nuovo")).toBeInTheDocument());
    expect(screen.getByTestId("new-folder").value).toBe("");
  });

  it("says when the PC does not answer", async () => {
    mockFetch([["/api/folders", () => json({ detail: "Il PC non risponde: il bridge non è attivo." }, 503)]]);
    render(<Settings />);
    await waitFor(() => expect(screen.getByTestId("folders-error")).toHaveTextContent("Il PC non risponde"));
  });
});
