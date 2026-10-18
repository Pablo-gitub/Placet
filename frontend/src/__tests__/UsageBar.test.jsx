import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
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
  it("shows each provider's windows and nothing without data", () => {
    const { rerender } = render(<UsageBar usage={[codex]} />);
    const row = screen.getByTestId("usage-codex:");
    expect(row).toHaveTextContent("Codex");
    expect(row.querySelector(".usage-high")).not.toBeNull(); // 95% of the 5-hour window
    rerender(<UsageBar usage={[]} />);
    expect(screen.queryByTestId("usage-bar")).toBeNull();
  });
});
