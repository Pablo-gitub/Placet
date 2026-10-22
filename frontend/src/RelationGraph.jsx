import React from "react";
import { useTranslation } from "react-i18next";
import { edgeActivities, edgeSchedules } from "./graphActivity";
import { formatDateTime } from "./i18n/formatters";

const SIZE = 320;
const CENTER = SIZE / 2;
const NODE_R = 20;
const MAX_LABEL = 14;

const short = (text, max = MAX_LABEL) => (text.length > max ? `${text.slice(0, max - 1)}…` : text);

/**
 * Node names often share a project prefix ("Placet_claude_FS",
 * "Placet_gemini_dev"): drop it in the drawing, up to its last separator,
 * so truncated labels stay distinguishable. The full name stays in the title.
 */
function commonPrefix(names) {
  if (names.length < 2) return "";
  let prefix = names[0];
  for (const name of names) {
    while (!name.startsWith(prefix)) prefix = prefix.slice(0, -1);
  }
  const cut = Math.max(...["_", "-", ".", " "].map((sep) => prefix.lastIndexOf(sep)));
  return cut > 0 && names.every((n) => n.length > cut + 1) ? prefix.slice(0, cut + 1) : "";
}

/** Nodes on a circle (one node in the middle), first one at the top. */
function layout(nodes) {
  if (nodes.length === 1) return { [nodes[0].id]: { x: CENTER, y: CENTER } };
  const radius = CENTER - NODE_R - 34;
  return Object.fromEntries(nodes.map((n, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / nodes.length;
    return [n.id, { x: CENTER + radius * Math.cos(angle), y: CENTER + radius * Math.sin(angle) }];
  }));
}

/**
 * One edge A -> B. Relations sharing a pair (both directions, or several
 * types) bend by a different amount, so each stays visible with its label.
 */
function edgePath(from, to, bend) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  const ux = dx / length;
  const uy = dy / length;
  // Start and end on the circles' edge, not their centre.
  const start = { x: from.x + ux * NODE_R, y: from.y + uy * NODE_R };
  const end = { x: to.x - ux * (NODE_R + 4), y: to.y - uy * (NODE_R + 4) };
  const control = { x: (start.x + end.x) / 2 - uy * bend, y: (start.y + end.y) / 2 + ux * bend };
  const label = { x: (start.x + 2 * control.x + end.x) / 4, y: (start.y + 2 * control.y + end.y) / 4 };
  // A point of the curve just past the sender's circle, where a queued prompt rests.
  const t = 0.16;
  const near = {
    x: (1 - t) ** 2 * start.x + 2 * (1 - t) * t * control.x + t * t * end.x,
    y: (1 - t) ** 2 * start.y + 2 * (1 - t) * t * control.y + t * t * end.y,
  };
  return { d: `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`, label, near };
}

const reducedMotion = () => typeof window !== "undefined"
  && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

/** RF-44: the project's relations drawn as a graph, with live activity. */
export default function RelationGraph({ nodes = [], relations = [], sessions = [], requests = [], projectId, onOpenSession }) {
  const { t } = useTranslation();
  if (nodes.length === 0) {
    return <p className="composer-note" data-testid="relation-graph-empty">{t("projects.graphEmpty")}</p>;
  }
  const pos = layout(nodes);
  const prefix = commonPrefix(nodes.map((n) => n.name));
  // Bend: pairs (unordered) get 0, ±1, ±2… steps; the reverse direction
  // bends the other way by construction of the perpendicular.
  const seen = {};
  const edges = relations.filter((r) => pos[r.from_node] && pos[r.to_node]).map((r) => {
    const key = [r.from_node, r.to_node].sort().join("-");
    const index = (seen[key] = (seen[key] ?? -1) + 1);
    const bend = r.from_node === r.to_node ? 0 : 22 + index * 18;
    return { rel: r, ...edgePath(pos[r.from_node], pos[r.to_node], bend) };
  });

  const busy = new Set(sessions.filter((x) => x.state === "busy" && !x.ended_at).map((x) => x.id));
  const activity = edgeActivities(requests);
  const schedules = edgeSchedules(requests);
  const still = reducedMotion();

  const handleNodeClick = (n) => {
    if (!n.session_id) return;
    if (onOpenSession) {
      onOpenSession(n.session_id, projectId);
    } else {
      window.location.assign(projectId ? `#/sessione/${n.session_id}?project=${projectId}` : `#/sessione/${n.session_id}`);
    }
  };

  return (
    <figure className="relation-graph" data-testid="relation-graph">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label={t("projects.graphTitle")}>
        <defs>
          <marker id="rg-arrow-automatic" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="rg-arrow-auto" />
          </marker>
          <marker id="rg-arrow-manual" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="rg-arrow-manual" />
          </marker>
          {/* RFC-16: a hollow ring at the sender's end when replies need review. */}
          <marker id="rg-reply-manual" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7">
            <circle cx="5" cy="5" r="3.5" className="rg-reply-ring" />
          </marker>
        </defs>
        {edges.map(({ rel, d, label, near }) => {
          const now = activity[rel.id];
          const ball = now && now !== "executing";
          return (
          <g key={rel.id} data-testid={`graph-edge-${rel.id}`} className={now ? `rg-${now}` : undefined}>
            <path d={d} className={`rg-edge rg-${rel.approval}`} markerEnd={`url(#rg-arrow-${rel.approval})`}
              markerStart={rel.reply_approval === "manual" ? "url(#rg-reply-manual)" : undefined}
              data-reply={rel.reply_approval || "manual"}>
              <title>{`${rel.from_name} → ${rel.to_name} · ${rel.type_name} · ${t(rel.approval === "automatic" ? "relations.automaticShort" : "relations.manualShort")} · ${t(rel.reply_approval === "automatic" ? "relations.replyAutomaticShort" : "relations.replyManualShort")}`}</title>
            </path>
            <text x={label.x} y={ball && (now === "waiting" || still) ? label.y - 9 : label.y}
              className="rg-edge-label" textAnchor="middle" dominantBaseline="middle">
              {short(rel.type_name)}
            </text>
            {ball && (
              <circle data-testid={`graph-ball-${rel.id}`} r={4.5} className={`rg-ball rg-ball-${now}`}
                {...(now === "queued" || now === "scheduled" ? { cx: near.x, cy: near.y }
                  : now === "waiting" || still ? { cx: label.x, cy: label.y } : {})}>
                {now !== "waiting" && now !== "queued" && now !== "scheduled" && !still && (
                  <animateMotion dur="1.6s" repeatCount="indefinite" path={d} calcMode="linear"
                    keyPoints={now === "returning" ? "1;0" : "0;1"} keyTimes="0;1" />
                )}
              </circle>
            )}
            {now === "scheduled" && (
              // A small clock beside the resting ball: held for its time, not stuck.
              <g data-testid={`graph-clock-${rel.id}`} className="rg-clock" transform={`translate(${near.x + 7} ${near.y - 7})`}>
                <title>{t("projects.graphScheduledAt", { time: formatDateTime(schedules[rel.id]) })}</title>
                <circle r={5} />
                <path d="M0 -3 V0 L2.5 1.5" />
              </g>
            )}
          </g>
          );
        })}
        {nodes.map((n) => (
          <g
            key={n.id}
            data-testid={`graph-node-${n.id}`}
            onClick={() => handleNodeClick(n)}
            className={[n.session_id && "rg-node-link", busy.has(n.session_id) && "rg-active"].filter(Boolean).join(" ") || undefined}
            role={n.session_id ? "link" : undefined}
            tabIndex={n.session_id ? 0 : undefined}
            aria-label={n.session_id ? n.name : undefined}
            onKeyDown={
              n.session_id
                ? (e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      handleNodeClick(n);
                    }
                  }
                : undefined
            }
          >
            <title>{n.name}</title>
            {busy.has(n.session_id) && <circle cx={pos[n.id].x} cy={pos[n.id].y} r={NODE_R} className={`rg-pulse prov-${n.provider}`} />}
            <circle cx={pos[n.id].x} cy={pos[n.id].y} r={NODE_R} className={`rg-node prov-${n.provider}`} />
            {/* Outside the circle of nodes, away from the edges that leave it. */}
            <text x={pos[n.id].x} y={pos[n.id].y < CENTER - 1 ? pos[n.id].y - NODE_R - 6 : pos[n.id].y + NODE_R + 13}
              className="rg-node-label" textAnchor="middle">
              {short(n.name.slice(prefix.length), 16)}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="relation-graph-legend">
        <span><i className="rg-swatch rg-automatic" /> {t("relations.automaticShort")}</span>
        <span><i className="rg-swatch rg-manual" /> {t("relations.manualShort")}</span>
        {relations.some((r) => r.reply_approval === "manual") && (
          <span data-testid="graph-legend-reply"><i className="rg-ring" /> {t("relations.replyManualShort")}</span>
        )}
        {Object.values(activity).includes("queued") && (
          <span data-testid="graph-legend-queued"><i className="rg-dot rg-ball-queued" /> {t("projects.graphQueued")}</span>
        )}
        {Object.values(activity).includes("scheduled") && (
          <span data-testid="graph-legend-scheduled"><i className="rg-dot rg-ball-scheduled" /> {t("projects.graphScheduled")}</span>
        )}
        {Object.values(activity).includes("waiting") && (
          <span data-testid="graph-legend-waiting"><i className="rg-dot rg-ball-waiting" /> {t("projects.graphWaiting")}</span>
        )}
      </figcaption>
    </figure>
  );
}
