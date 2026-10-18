import { useTranslation } from "react-i18next";
import React, { useState } from "react";
import { Check, Copy } from "lucide-react";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

// The languages agents write most; anything else is shown as plain text.
const LANGUAGES = {
  bash, css, diff, go, ini, java, javascript, json, markdown, python, rust, sql, typescript, xml, yaml,
};
for (const [name, language] of Object.entries(LANGUAGES)) hljs.registerLanguage(name, language);
const ALIASES = {
  sh: "bash", shell: "bash", zsh: "bash", console: "bash", js: "javascript", jsx: "javascript",
  ts: "typescript", tsx: "typescript", py: "python", html: "xml", svg: "xml", yml: "yaml",
  toml: "ini", md: "markdown", rs: "rust", patch: "diff",
};

function languageOf(className) {
  const match = /language-([\w+-]+)/.exec(className || "");
  if (!match) return null;
  const name = match[1].toLowerCase();
  return ALIASES[name] || name;
}

/**
 * A fenced code block: highlighted when its language is known, with the
 * language's name and a copy button, like the desktop apps.
 * highlight.js escapes the source, so its output is safe to insert.
 */
export default function CodeBlock({ className, children }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const source = String(children ?? "").replace(/\n$/, "");
  const language = languageOf(className);
  const known = language && hljs.getLanguage(language);
  const html = known ? hljs.highlight(source, { language, ignoreIllegals: true }).value : null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(source);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // no clipboard (insecure context): nothing to do
    }
  };

  return (
    <div className="code-block" data-testid="code-block">
      <div className="code-block-bar">
        <span>{language || t("viewer.codeText")}</span>
        <button type="button" onClick={copy} aria-label={t("viewer.copyCode")} data-testid="code-copy">
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {t(copied ? "viewer.copied" : "viewer.copy")}
        </button>
      </div>
      <pre>
        {html !== null ? (
          <code className={`hljs language-${language}`} dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <code>{source}</code>
        )}
      </pre>
    </div>
  );
}
