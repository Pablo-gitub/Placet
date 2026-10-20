import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, afterEach } from "vitest";
import Settings from "../Settings";
import Statistics from "../Statistics";

const sampleStats = {
  from: "2026-10-14",
  to: "2026-10-20",
  tz: "UTC",
  total_requests: 20,
  per_day: [
    { date: "2026-10-14", count: 2, mean_reply_seconds: 40.0 },
    { date: "2026-10-15", count: 5, mean_reply_seconds: 55.0 },
    { date: "2026-10-16", count: 3, mean_reply_seconds: 30.0 },
    { date: "2026-10-17", count: 0, mean_reply_seconds: null },
    { date: "2026-10-18", count: 4, mean_reply_seconds: 90.0 },
    { date: "2026-10-19", count: 1, mean_reply_seconds: 15.0 },
    { date: "2026-10-20", count: 5, mean_reply_seconds: 45.0 },
  ],
  by_relation_type: {
    Architettura: 12,
    Consulenza: 6,
  },
  by_project: [
    { id: 1, name: "Placet", count: 15 },
    { id: null, name: "Senza progetto", count: 5 },
  ],
  by_sender_provider: {
    "claude-code": 10,
    gemini: 5,
    codex: 3,
    human: 2,
  },
  by_recipient_provider: {
    "claude-code": 4,
    gemini: 8,
    codex: 4,
    human: 4,
  },
  approval: {
    automatic: 14,
    manual: 4,
    ad_hoc: 2,
  },
  decisions: {
    approved: 12,
    edited: 2,
    rejected: 3,
    expired: 1,
    cancelled: 0,
  },
  user_messages: {
    requests: 2,
    replies: 4,
  },
  mean_reply_seconds: 45.8,
  usage: [
    {
      provider: "claude-code",
      window: "5h",
      bucket_id: "5h",
      bucket_group: null,
      threshold: 80,
      threshold_days: 2,
      peaks: [
        { date: "2026-10-14", peak_used_percent: 50.0 },
        { date: "2026-10-15", peak_used_percent: 65.0 },
        { date: "2026-10-16", peak_used_percent: 70.0 },
        { date: "2026-10-17", peak_used_percent: 40.0 },
        { date: "2026-10-18", peak_used_percent: 85.0 },
        { date: "2026-10-19", peak_used_percent: 90.0 },
        { date: "2026-10-20", peak_used_percent: 60.0 },
      ],
    },
  ],
};

function mockStatsFetch(stats = sampleStats) {
  const calls = [];
  global.fetch = vi.fn((url) => {
    calls.push(url);
    if (url.startsWith("/api/stats")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(stats),
      });
    }
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    });
  });
  return calls;
}

describe("Statistics view (RF-64)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("links to statistics from Settings under records section", () => {
    const onOpenStats = vi.fn();
    render(<Settings onOpenStats={onOpenStats} />);
    const link = screen.getByTestId("settings-stats");
    expect(link).toBeInTheDocument();
    fireEvent.click(link);
    expect(onOpenStats).toHaveBeenCalledTimes(1);
  });

  it("renders summary cards, SVG charts, and distributions", async () => {
    mockStatsFetch();
    render(<Statistics onBack={vi.fn()} />);

    // Wait for data load
    await waitFor(() => expect(screen.getByTestId("stats-total-requests")).toHaveTextContent("20"));

    // Automation rate: 14 / 20 = 70%
    expect(screen.getByTestId("stats-automation-rate")).toHaveTextContent("70%");

    // Mean reply time: 45.8s -> 46s
    expect(screen.getByTestId("stats-mean-reply")).toHaveTextContent("46s");

    // Days above threshold: 2
    expect(screen.getByTestId("stats-threshold-days")).toHaveTextContent("2");

    // Check SVG volume chart exists
    expect(screen.getByTestId("stats-volume-chart")).toBeInTheDocument();

    // Check relation types
    expect(screen.getByTestId("stats-relations")).toHaveTextContent("Architettura");
    expect(screen.getByTestId("stats-relations")).toHaveTextContent("12");

    // Check projects
    expect(screen.getByTestId("stats-projects")).toHaveTextContent("Placet");
    expect(screen.getByTestId("stats-projects")).toHaveTextContent("Senza progetto");

    // Check provider usage chart uses provider label "Claude"
    const usageChart = screen.getByTestId("stats-usage-chart-claude-code-5h");
    expect(usageChart).toBeInTheDocument();
    expect(usageChart).toHaveTextContent("Claude");

    // Check decisions section renders all outcomes
    const outcomesSection = screen.getByTestId("stats-outcomes");
    expect(outcomesSection).toBeInTheDocument();
    expect(outcomesSection).toHaveTextContent("12"); // approved
    expect(outcomesSection).toHaveTextContent("2");  // edited
    expect(outcomesSection).toHaveTextContent("3");  // rejected
    expect(outcomesSection).toHaveTextContent("1");  // expired
    expect(outcomesSection).toHaveTextContent("0");  // cancelled
  });

  it("does not render bar or percentage for days without usage samples", async () => {
    const statsWithNull = {
      ...sampleStats,
      usage: [
        {
          provider: "claude-code",
          window: "5h",
          bucket_id: "5h",
          bucket_group: null,
          threshold: 80,
          threshold_days: 0,
          peaks: [
            { date: "2026-10-14", peak_used_percent: null },
            { date: "2026-10-15", peak_used_percent: 75.0 },
          ],
        },
      ],
    };
    mockStatsFetch(statsWithNull);
    render(<Statistics onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId("stats-usage-chart-claude-code-5h")).toBeInTheDocument());

    const usageChart = screen.getByTestId("stats-usage-chart-claude-code-5h");
    // Day 2026-10-14 has peak_used_percent: null, so it shouldn't show a bar or percentage label
    const nullDayGroup = usageChart.querySelector('[data-date="2026-10-14"]');
    expect(nullDayGroup.querySelector("rect.stats-chart-bar")).toBeNull();
    expect(nullDayGroup.querySelector(".stats-chart-val-text")).toBeNull();
    expect(nullDayGroup).not.toHaveTextContent("%");
    // Day 2026-10-15 has peak_used_percent: 75, so it should render the bar and text
    expect(usageChart.querySelector('[data-date="2026-10-15"] rect.stats-chart-bar')).not.toBeNull();
    expect(usageChart).toHaveTextContent("75%");
  });

  it("changes period using chips (today, 7d, 30d, custom)", async () => {
    const calls = mockStatsFetch();
    render(<Statistics onBack={vi.fn()} />);

    await waitFor(() => expect(calls.length).toBeGreaterThan(0));

    // Default chip is 7 days
    expect(screen.getByTestId("period-chip-7d")).toHaveClass("active");

    // Click "today"
    fireEvent.click(screen.getByTestId("period-chip-today"));
    await waitFor(() => {
      const lastCall = calls[calls.length - 1];
      expect(lastCall).toContain("/api/stats?");
    });

    // Click "30d"
    fireEvent.click(screen.getByTestId("period-chip-30d"));
    await waitFor(() => {
      const lastCall = calls[calls.length - 1];
      expect(lastCall).toContain("/api/stats?");
    });

    // Click "custom"
    fireEvent.click(screen.getByTestId("period-chip-custom"));
    expect(screen.getByTestId("stats-custom-from")).toBeInTheDocument();
    expect(screen.getByTestId("stats-custom-to")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("stats-custom-from"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByTestId("stats-custom-to"), { target: { value: "2026-10-15" } });
    fireEvent.click(screen.getByTestId("stats-custom-apply"));

    await waitFor(() => {
      const lastCall = calls[calls.length - 1];
      expect(lastCall).toContain("from=2026-10-01");
      expect(lastCall).toContain("to=2026-10-15");
    });
  });

  it("displays empty usage notice when no usage samples exist", async () => {
    const emptyStats = { ...sampleStats, usage: [] };
    mockStatsFetch(emptyStats);
    render(<Statistics onBack={vi.fn()} />);

    await waitFor(() => expect(screen.getByTestId("stats-no-usage")).toBeInTheDocument());
  });

  it("displays error message when stats fetch fails", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: () => Promise.resolve({ code: "stats.invalid_date" }),
    });
    render(<Statistics onBack={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("Formato data non valido (atteso AAAA-MM-GG).");
    });
  });

  it("calls onBack when back button is clicked", async () => {
    mockStatsFetch();
    const onBack = vi.fn();
    render(<Statistics onBack={onBack} />);
    fireEvent.click(screen.getByTestId("stats-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});
