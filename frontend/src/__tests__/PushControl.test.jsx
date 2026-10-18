import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect } from "vitest";
import i18n from "../i18n";
import PushControl from "../PushControl";

it("explains how to get notifications where Web Push is not available", () => {
  render(<PushControl />);
  fireEvent.click(screen.getByTestId("push-control"));
  expect(screen.getByRole("dialog")).toHaveTextContent(i18n.t("push.install"));
  expect(screen.queryByTestId("push-enable")).toBeNull();
});
