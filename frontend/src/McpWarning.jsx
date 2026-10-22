import React from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";
import { mcpWarning } from "./aspects";

const GUIDE = "https://github.com/Pablo-gitub/Placet/blob/main/docs/installation.md#3-configure-the-placet-mcp-server";

/**
 * RF-27: the session cannot use Placet's tools (no MCP server configured, or
 * none ever used). `compact` is the Home row badge; otherwise a line for the
 * session header with a pointer to the installation guide.
 */
export default function McpWarning({ session, compact = false }) {
  const { t } = useTranslation();
  if (!mcpWarning(session)) return null;
  if (compact) {
    return (
      <span className="badge-mcp" title={`${t("mcp.warning")}. ${t("mcp.hint")}`} data-testid={`session-mcp-${session.id}`}>
        <AlertTriangle size={14} aria-label={t("mcp.warning")} />
      </span>
    );
  }
  return (
    <p className="mcp-warning" role="status" data-testid="session-view-mcp">
      <AlertTriangle size={14} aria-hidden="true" /> <strong>{t("mcp.warning")}</strong> {t("mcp.hint")}{" "}
      <a href={GUIDE} target="_blank" rel="noopener noreferrer">{t("mcp.guide")}</a>
    </p>
  );
}
