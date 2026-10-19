import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import i18n from "../i18n";
import UsageBar from "../UsageBar";
import { untilReset, usageRows } from "../usage";

const NOW = new Date("2026-10-18T18:00:00Z").getTime();
const codex = {
  provider: "codex", plan: "plus", observed_at: "2026-10-18T17:48:00Z",
  buckets: [
    { id: "5h", group: null, window: "5h", used_percent: 95, resets_at: "2026-10-18T19:20:00Z" },
    { id: "7d", group: null, window: "7d", used_percent: 26, resets_at: "2026-10-23T23:00:00Z" },
  ],
};
const antigravity = {
  provider: "antigravity",
  buckets: [
    { id: "g5", group: "Gemini Models", window: "5h", used_percent: 12, resets_at: "2026-10-18T20:00:00Z" },
    { id: "c5", group: "Claude and GPT models", window: "5h", used_percent: 40, resets_at: "2026-10-18T17:00:00Z" },
  ],
};

describe("usage rows", () => {
  it("makes one row per provider, or per model group", () => {
    const rows = usageRows([codex, antigravity], NOW);
    expect(rows.map((r) => r.key)).toEqual(["codex:", "antigravity:Gemini Models", "antigravity:Claude and GPT models"]);
    expect(usageRows([antigravity, codex], NOW)[0].provider).toBe("codex");
    expect(rows[1].group).toBe("Gemini");
    expect(rows[0].windows["5h"].used).toBe(95);
  });

  it("treats a window whose reset has passed as unused", () => {
    const row = usageRows([antigravity], NOW).find((r) => r.group === "Claude and GPT");
    expect(row.windows["5h"]).toEqual({ used: 0, resetsAt: null });
  });

  it("ignores malformed data instead of failing", () => {
    expect(usageRows({ detail: "x" })).toEqual([]);
    expect(usageRows([{ provider: "codex" }])).toEqual([]);
  });

  it("says how long until the reset", () => {
    const t = i18n.getFixedT("en");
    expect(untilReset("2026-10-18T18:45:00Z", t, NOW)).toBe("45 min");
    expect(untilReset("2026-10-18T19:20:00Z", t, NOW)).toBe("1h20");
    expect(untilReset("2026-10-23T23:00:00Z", t, NOW)).toBe("5d 5h");
  });
});

describe("UsageBar", () => {
  afterEach(() => vi.useRealTimers());

  it("shows each provider's windows and nothing without data", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    const { rerender } = render(<UsageBar usage={[codex]} />);
    const row = screen.getByTestId("usage-codex:");
    expect(row).toHaveTextContent("Codex");
    expect(row.querySelector(".usage-high")).not.toBeNull(); // 95% of the 5-hour window
    rerender(<UsageBar usage={[]} />);
    expect(screen.queryByTestId("usage-bar")).toBeNull();
  });
});

describe("usage limit", () => {
  it("knows when a provider at its limit takes messages again", async () => {
    const { limitReset } = await import("../usage");
    expect(limitReset([{ ...codex, buckets: [{ ...codex.buckets[0], used_percent: 100 }] }], "codex", NOW))
      .toBe("2026-10-18T19:20:00Z");
    expect(limitReset([codex], "codex", NOW)).toBeNull(); // 95%: not yet
    // Per-model groups (Antigravity) do not hold messages.
    expect(limitReset([{ provider: "antigravity", buckets: [{ ...antigravity.buckets[0], used_percent: 100 }] }],
      "antigravity", NOW)).toBeNull();
    // Past the reset: free again.
    expect(limitReset([{ ...codex, buckets: [{ ...codex.buckets[0], used_percent: 100 }] }], "codex",
      new Date("2026-10-18T20:00:00Z").getTime())).toBeNull();
  });
});

describe("stale usage", () => {
  it("shows an unknown value, not 0%, when a polled provider's data predates the reset", () => {
    const old = { provider: "claude-code", observed_at: "2026-10-18T08:00:00Z", buckets: [
      { id: "five_hour", group: null, window: "5h", used_percent: 40, resets_at: "2026-10-18T10:00:00Z" },
      { id: "seven_day", group: null, window: "7d", used_percent: 30, resets_at: "2026-10-23T10:00:00Z" },
    ] };
    const [row] = usageRows([old], NOW);
    expect(row.windows["5h"].used).toBeNull();
    expect(row.windows["7d"].used).toBe(30);
    expect(row.staleSince).toBe(old.observed_at);
    // Codex reports on use: no newer data after a reset means nothing was used.
    const [codexRow] = usageRows([{ ...old, provider: "codex" }], NOW);
    expect(codexRow.windows["5h"].used).toBe(0);
    expect(codexRow.staleSince).toBeNull();
  });
});

describe("Claude auto-renew menu in UsageBar", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches auto-renew setting and toggles it", async () => {
    const claudeUsage = {
      provider: "claude-code", observed_at: "2026-10-18T17:48:00Z",
      buckets: [
        { id: "five_hour", group: null, window: "5h", used_percent: 50, resets_at: "2026-10-18T19:20:00Z" },
      ],
    };

    global.fetch = vi.fn().mockImplementation((url, opts) => {
      if (url === "/api/settings/claude-renew" && (!opts || opts.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ enabled: false }),
        });
      }
      if (url === "/api/settings/claude-renew" && opts?.method === "PUT") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ enabled: true }),
        });
      }
      return Promise.reject(new Error(`unexpected fetch ${url}`));
    });

    const { fireEvent } = await import("@testing-library/react");
    render(<UsageBar usage={[claudeUsage]} />);

    // Menu button on Claude row
    const menuBtn = screen.getByTestId("btn-claude-menu");
    expect(menuBtn).not.toBeNull();
    fireEvent.click(menuBtn);

    // Toggle checkbox appears
    const toggle = await screen.findByTestId("toggle-claude-auto-renew");
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);
    expect(global.fetch).toHaveBeenCalledWith("/api/settings/claude-renew", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ enabled: true }),
    }));
  });
});

