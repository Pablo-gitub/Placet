import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import TurnErrorNotice from "../TurnErrorNotice";
import i18n from "../i18n";

const session = (over = {}) => ({ id: "s1", provider: "claude-code", state: "idle", turn_error: null, ...over });

describe("a turn stopped by an API error", () => {
  it("says the usage limit stopped the session and that it will not resume by itself", () => {
    render(<TurnErrorNotice session={session({ turn_error: "rate_limit", turn_error_at: "2026-10-23T00:30:00Z" })} />);
    expect(screen.getByTestId("turn-error")).toHaveTextContent(i18n.t("turnError.rateLimit"));
  });

  it("names any other error by its code", () => {
    render(<TurnErrorNotice session={session({ turn_error: "overloaded" })} />);
    expect(screen.getByTestId("turn-error")).toHaveTextContent("overloaded");
  });

  it("shows nothing without an error or while the session works again", () => {
    const { container, rerender } = render(<TurnErrorNotice session={session()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<TurnErrorNotice session={session({ turn_error: "rate_limit", state: "busy" })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("has both languages", () => {
    for (const lng of ["it", "en"]) {
      expect(i18n.getResource(lng, "translation", "turnError.rateLimit")).toBeTruthy();
      expect(i18n.getResource(lng, "translation", "turnError.other")).toBeTruthy();
    }
  });
});
