import React from "react";
import { useTranslation } from "react-i18next";

const SIZE = 320;
const CENTER = SIZE / 2;
const NODE_R = 20;
const MAX_LABEL = 14;

const short = (text) => (text.length > MAX_LABEL ? `${text.slice(0, MAX_LABEL - 1)}…` : text);

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
  return { d: `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`, label };
}

/** RF-44: the project's relations drawn as a graph (read only). */
export default function RelationGraph({ nodes = [], relations = [] }) {
  const { t } = useTranslation();
  if (nodes.length === 0) {
    return <p className="composer-note" data-testid="relation-graph-empty">{t("projects.graphEmpty")}</p>;
  }
  const pos = layout(nodes);
  // Bend: pairs (unordered) get 0, ±1, ±2… steps; the reverse direction
  // bends the other way by construction of the perpendicular.
  const seen = {};
  const edges = relations.filter((r) => pos[r.from_node] && pos[r.to_node]).map((r) => {
    const key = [r.from_node, r.to_node].sort().join("-");
    const index = (seen[key] = (seen[key] ?? -1) + 1);
    const bend = r.from_node === r.to_node ? 0 : 22 + index * 18;
    return { rel: r, ...edgePath(pos[r.from_node], pos[r.to_node], bend) };
  });

  return (
    <figure className="relation-graph" data-testid="relation-graph">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label={t("projects.graphTitle")}>
        <defs>
          <marker id="rg-arrow-auto" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="rg-arrow-auto" />
          </marker>
          <marker id="rg-arrow-manual" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="rg-arrow-manual" />
          </marker>
        </defs>
        {edges.map(({ rel, d, label }) => (
          <g key={rel.id} data-testid={`graph-edge-${rel.id}`}>
            <path d={d} className={`rg-edge rg-${rel.approval}`} markerEnd={`url(#rg-arrow-${rel.approval})`}>
              <title>{`${rel.from_name} → ${rel.to_name} · ${rel.type_name} · ${t(rel.approval === "automatic" ? "relations.automaticShort" : "relations.manualShort")}`}</title>
            </path>
            <text x={label.x} y={label.y} className="rg-edge-label" textAnchor="middle" dominantBaseline="middle">
              {short(rel.type_name)}
            </text>
          </g>
        ))}
        {nodes.map((n) => (
          <g key={n.id} data-testid={`graph-node-${n.id}`}>
            <circle cx={pos[n.id].x} cy={pos[n.id].y} r={NODE_R} className={`rg-node prov-${n.provider}`} />
            <text x={pos[n.id].x} y={pos[n.id].y + NODE_R + 13} className="rg-node-label" textAnchor="middle">
              {short(n.name)}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="relation-graph-legend">
        <span><i className="rg-swatch rg-automatic" /> {t("relations.automaticShort")}</span>
        <span><i className="rg-swatch rg-manual" /> {t("relations.manualShort")}</span>
      </figcaption>
    </figure>
  );
}
