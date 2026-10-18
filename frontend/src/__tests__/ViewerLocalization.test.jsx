import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import i18n from "../i18n";
import Attachments from "../Attachments";
import PdfViewer from "../PdfViewer";

const pdf = vi.hoisted(() => ({ getDocument: vi.fn(), destroy: vi.fn(), getPage: vi.fn() }));
vi.mock("pdfjs-dist", () => ({ getDocument: pdf.getDocument, GlobalWorkerOptions: {} }));
vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({ default: "/mock-worker.js" }));

beforeEach(() => {
  pdf.getDocument.mockReset(); pdf.destroy.mockReset(); pdf.getPage.mockReset();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ fillRect: vi.fn() });
  pdf.getPage.mockResolvedValue({ getViewport: ({ scale }) => ({ width: 100 * scale, height: 200 * scale }), render: () => ({ promise: Promise.resolve() }) });
  pdf.getDocument.mockReturnValue({ promise: Promise.resolve({ numPages: 2, getPage: pdf.getPage }), destroy: pdf.destroy });
});
afterEach(() => { vi.restoreAllMocks(); });

it.each(["en", "it"])("localizes an image viewer, keeps the filename and closes with Escape in %s", async language => {
  await i18n.changeLanguage(language);
  render(<Attachments sessionId="c1" items={[{ id: "1:0:0", name: "immagine.png", kind: "image" }]} />);
  fireEvent.click(screen.getByLabelText(i18n.t("viewer.open", { name: "immagine.png" })));
  const dialog = screen.getByRole("dialog", { name: "immagine.png" });
  expect(within(dialog).getByLabelText(i18n.t("viewer.download"))).toHaveAttribute("download", "immagine.png");
  await act(async () => { await i18n.changeLanguage(language === "en" ? "it" : "en"); });
  expect(within(dialog).getByLabelText(i18n.t("viewer.close"))).toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("translates an already-visible image failure", async () => {
  render(<Attachments sessionId="c1" items={[{ id: "1:0:0", name: "foto.png" }]} />);
  fireEvent.error(screen.getByAltText("foto.png"));
  expect(screen.getByTestId("attachment-missing-1:0:0")).toHaveTextContent("PC non raggiungibile");
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByTestId("attachment-missing-1:0:0")).toHaveTextContent("Image unavailable");
});

it("updates PDF page counts without reloading or duplicating canvases", async () => {
  const close = vi.fn();
  render(<PdfViewer url="/document.pdf" name="Rapporto.pdf" onClose={close} />);
  await waitFor(() => expect(screen.getByText(/2 pagine/)).toBeInTheDocument());
  await waitFor(() => expect(pdf.getPage).toHaveBeenCalledTimes(2));
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByText(/2 pages/)).toBeInTheDocument();
  expect(pdf.getDocument).toHaveBeenCalledTimes(1);
  expect(document.querySelectorAll(".pdf-page")).toHaveLength(2);
  expect(screen.getByLabelText("Download")).toHaveAttribute("download", "Rapporto.pdf");
  fireEvent.click(screen.getByLabelText("Close"));
  expect(close).toHaveBeenCalledOnce();
});

it("localizes PDF failures and does not render raw library diagnostics", async () => {
  pdf.getDocument.mockReturnValue({ promise: Promise.reject(new Error("private file path")), destroy: pdf.destroy });
  render(<PdfViewer url="/bad.pdf" name="bad.pdf" onClose={vi.fn()} />);
  expect(await screen.findByTestId("pdf-error")).not.toHaveTextContent("private file path");
  await act(async () => { await i18n.changeLanguage("en"); });
  expect(screen.getByTestId("pdf-error")).toHaveTextContent("Unable to open the PDF");
});
