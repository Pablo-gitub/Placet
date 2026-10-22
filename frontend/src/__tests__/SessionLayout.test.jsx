import React from "react";
import fs from "node:fs";
import path from "node:path";
import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Composer from "../Composer";
import i18n from "../i18n";

const session = { id: "s1", agent_id: "claude", provider: "claude-code", reachable: true };
const css = fs.readFileSync(path.resolve(__dirname, "../index.css"), "utf8");

function narrow(matches) {
  window.matchMedia = (query) => ({
    matches: matches && query.includes("max-width"), media: query,
    addEventListener() {}, removeEventListener() {},
  });
}

describe("session view on a narrow phone", () => {
  const original = window.matchMedia;
  afterEach(async () => {
    window.matchMedia = original;
    await i18n.changeLanguage("it");
  });

  it("puts the title block on its own full-width line below the compact controls", () => {
    expect(css).toMatch(/\.session-view\s*\{[^}]*container:\s*session\s*\/\s*inline-size/);
    const rule = css.match(/@container session \(max-width: \d+px\)\s*\{([\s\S]*?)\n\}/);
    expect(rule).not.toBeNull();
    expect(rule[1]).toMatch(/\.session-view-header\s*\{[^}]*flex-wrap:\s*wrap/);
    expect(rule[1]).toMatch(/\.session-view-title\s*\{[^}]*flex:\s*1 0 100%/);
    expect(rule[1]).toMatch(/\.session-view-title\s*\{[^}]*order:\s*1/);
  });

  it("uses a short placeholder that fits one line when the screen is narrow", () => {
    narrow(true);
    render(<Composer session={session} onSend={vi.fn()} />);
    expect(screen.getByTestId("composer-input").getAttribute("placeholder")).toBe(i18n.t("composer.placeholderShort"));
    expect(i18n.t("composer.placeholderShort").length).toBeLessThan(i18n.t("composer.placeholder").length);
  });

  it("keeps the full placeholder on a wide screen", () => {
    narrow(false);
    render(<Composer session={session} onSend={vi.fn()} />);
    expect(screen.getByTestId("composer-input").getAttribute("placeholder")).toBe(i18n.t("composer.placeholder"));
  });

  it("shows a visible hint inside the empty schedule field, and hides it once a time is set", async () => {
    render(<Composer session={session} onSend={vi.fn()} />);
    const hint = screen.getByTestId("composer-schedule-hint");
    expect(hint).toHaveTextContent(i18n.t("composer.scheduleHint"));
    expect(hint.getAttribute("aria-hidden")).toBe("true");
    fireEvent.change(screen.getByTestId("composer-schedule-input"), { target: { value: "2026-10-25T15:30" } });
    expect(screen.queryByTestId("composer-schedule-hint")).toBeNull();
    fireEvent.click(screen.getByTestId("composer-schedule-clear"));
    expect(screen.getByTestId("composer-schedule-hint")).toBeInTheDocument();
  });

  it("has the new texts in both languages", async () => {
    for (const lng of ["it", "en"]) {
      await i18n.changeLanguage(lng);
      expect(i18n.t("composer.scheduleHint")).not.toBe("composer.scheduleHint");
      expect(i18n.t("composer.placeholderShort")).not.toBe("composer.placeholderShort");
    }
  });
});
