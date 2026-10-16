import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import CodeBlock from "./CodeBlock";

/**
 * Renders agent-written Markdown. The text comes from agents, so raw HTML is
 * dropped (skipHtml) and react-markdown's default URL filter blocks
 * javascript: links: a prompt must not be able to run code in the page
 * where requests are approved.
 */
export default function Markdown({ children, testId }) {
  return (
    <div className="markdown" data-testid={testId}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children: text }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {text}
            </a>
          ),
          // Fenced blocks become CodeBlock; inline code stays a plain <code>.
          pre: ({ children: block }) => {
            const code = React.Children.toArray(block)[0];
            return <CodeBlock className={code?.props?.className}>{code?.props?.children}</CodeBlock>;
          },
          table: ({ children: rows }) => (
            <div className="markdown-table">
              <table>{rows}</table>
            </div>
          ),
        }}
      >
        {children || ""}
      </ReactMarkdown>
    </div>
  );
}
