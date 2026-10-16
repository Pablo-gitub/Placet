import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import RequestCard from "../RequestCard";
import Markdown from "../Markdown";

const now = new Date().toISOString();
const review = {
  id: 80, sender_id: "claude", recipient_id: "gemini", question_type: "prompt_review",
  prompt: "# Compito\n\n- verifica i **casi limite**\n\n```py\nprint(1)\n```",
  context: "Serve per il rilascio di _venerdì_", prompt_source: "PROMPT_GEMINI.md",
  options: [], status: "PENDING", created_at: now,
};

describe("Markdown", () => {
  it("renders headings, lists, emphasis and code", () => {
    render(<Markdown testId="md">{review.prompt}</Markdown>);
    const md = screen.getByTestId("md");
    expect(md.querySelector("h1")).toHaveTextContent("Compito");
    expect(md.querySelector("li strong")).toHaveTextContent("casi limite");
    expect(md.querySelector("pre code")).toHaveTextContent("print(1)");
  });

  it("never renders raw HTML or javascript: links from agents", () => {
    render(<Markdown testId="md">{'<img src=x onerror="alert(1)"><script>alert(2)</script>\n\n[x](javascript:alert(3))'}</Markdown>);
    const md = screen.getByTestId("md");
    expect(md.querySelector("img")).toBeNull();
    expect(md.querySelector("script")).toBeNull();
    const link = md.querySelector("a");
    expect(link?.getAttribute("href") || "").not.toContain("javascript:");
  });
});

describe("prompt review card", () => {
  it("shows context, rendered prompt with its file, and the question as separate sections", () => {
    render(<RequestCard req={review} onDecide={vi.fn()} />);
    expect(screen.getByTestId("context-80").querySelector("em")).toHaveTextContent("venerdì");
    expect(screen.getByTestId("prompt-preview-80").querySelector("h1")).toHaveTextContent("Compito");
    expect(screen.getByTestId("prompt-source-80")).toHaveTextContent("PROMPT_GEMINI.md");
    expect(screen.getByText(/Riceverà solo il prompt, non il contesto/)).toBeInTheDocument();
  });

  it("edits the raw Markdown and sends the edited version", () => {
    const onDecide = vi.fn();
    render(<RequestCard req={review} onDecide={onDecide} />);
    fireEvent.click(screen.getByTestId("toggle-edit-80"));
    const textarea = screen.getByTestId("prompt-textarea-80");
    expect(textarea.value).toBe(review.prompt);
    fireEvent.change(textarea, { target: { value: "# Compito\n\nsolo i casi limite" } });
    fireEvent.click(screen.getByTestId("toggle-edit-80"));  // back to preview
    expect(screen.getByTestId("prompt-preview-80")).toHaveTextContent("solo i casi limite");
    expect(screen.getByText(/Modificato da te/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("btn-approve-80"));
    expect(onDecide).toHaveBeenCalledWith(80, {
      status: "APPROVED", selected_options: [], edited_prompt: "# Compito\n\nsolo i casi limite",
    });
  });
});

describe("code blocks", () => {
  it("highlights a known language, names it, and copies the source", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });
    render(<Markdown testId="md">{"Esegui:\n\n```sh\ngit status # stato\n```\n\nPoi `npm test`."}</Markdown>);
    const block = screen.getByTestId("code-block");
    expect(block).toHaveTextContent("bash");
    expect(block.querySelector(".hljs-comment")).toHaveTextContent("# stato");
    expect(screen.getByTestId("md").querySelector("p code")).toHaveTextContent("npm test"); // inline stays inline
    fireEvent.click(screen.getByTestId("code-copy"));
    expect(writeText).toHaveBeenCalledWith("git status # stato");
    expect(await screen.findByText("Copiato")).toBeInTheDocument();
  });

  it("shows unknown languages as plain, escaped text", () => {
    render(<Markdown testId="md">{"```brainfuck\n<b>++</b>\n```"}</Markdown>);
    const block = screen.getByTestId("code-block");
    expect(block.querySelector("b")).toBeNull();
    expect(block).toHaveTextContent("<b>++</b>");
  });

  it("escapes highlighted code too", () => {
    render(<Markdown testId="md">{'```html\n<img src=x onerror="alert(1)">\n```'}</Markdown>);
    expect(screen.getByTestId("code-block").querySelector("img")).toBeNull();
  });
});
