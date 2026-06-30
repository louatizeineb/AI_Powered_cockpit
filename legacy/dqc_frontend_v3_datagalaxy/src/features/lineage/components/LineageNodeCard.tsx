import ExpandButton from "./ExpandButton";
import type { HighlightDirection, LineageDirection, LineageNode } from "../types/lineage.types";
import { colorForNode, iconForNode, typeLabelForNode } from "../utils/lineageStyles";
import type { GroupedChildItem } from "../utils/lineageGrouping";
import LineageNodeMenu from "./LineageNodeMenu";

type LineageNodeCardProps = {
  node: LineageNode;
  focused: boolean;
  highlightColor?: string | null;
  hasHighlight: boolean;
  groupedChildren: GroupedChildItem[];
  expanded: {
    upstream: Record<string, boolean>;
    downstream: Record<string, boolean>;
  };
  loading: Record<string, boolean>;
  onFocus: (nodeId: string) => void;
  onExpand: (nodeId: string, direction: LineageDirection) => void;
  onHighlight: (nodeId: string, direction: HighlightDirection, color: string) => void;
  onClearNodeHighlights: (nodeId: string) => void;
  onClearAllHighlights: () => void;
};

function compact(value: string | null | undefined, max = 46) {
  const text = String(value || "");
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

function loadingKey(nodeId: string, direction: LineageDirection) {
  return `${nodeId}:${direction}`;
}

function categoryOf(node: LineageNode) {
  return String(node.category || node.type || "").toLowerCase();
}

function isGroupedEntity(node: LineageNode) {
  const category = categoryOf(node);
  return category.includes("field") || category.includes("processing_item") || category.includes("processingitem");
}

function cardTitle(node: LineageNode) {
  if (isGroupedEntity(node) && node.parent_label) return node.parent_label;
  return node.group_label || node.label;
}

function cardSubtitle(node: LineageNode) {
  if (isGroupedEntity(node)) return node.path || node.group_label || node.type || "Lineage card";
  return node.parent_label || node.path || "Lineage card";
}

function primaryRowRole(node: LineageNode) {
  const category = categoryOf(node);
  if (category.includes("processing_item") || category.includes("processingitem")) return "DPI";
  if (category.includes("field")) return "Field";
  return "";
}

export default function LineageNodeCard({
  node,
  focused,
  highlightColor,
  hasHighlight,
  groupedChildren,
  expanded,
  loading,
  onFocus,
  onExpand,
  onHighlight,
  onClearNodeHighlights,
  onClearAllHighlights,
}: LineageNodeCardProps) {
  const color = colorForNode(node);
  const upstreamTarget =
    node.has_upstream && !expanded.upstream[node.id]
      ? node.id
      : groupedChildren.find((child) => child.nodeId && child.hasUpstream && !expanded.upstream[child.nodeId])?.nodeId;
  const downstreamTarget =
    node.has_downstream && !expanded.downstream[node.id]
      ? node.id
      : groupedChildren.find((child) => child.nodeId && child.hasDownstream && !expanded.downstream[child.nodeId])?.nodeId;
  const primaryRole = primaryRowRole(node);

  return (
    <article
      className={`plex-node-card ${focused ? "focused" : ""} ${highlightColor ? "highlighted" : ""}`}
      style={{ borderLeftColor: color, ["--plex-highlight-color" as string]: highlightColor || "transparent" }}
      onClick={() => onFocus(node.id)}
      tabIndex={0}
    >
      {upstreamTarget && (
        <ExpandButton
          direction="upstream"
          loading={loading[loadingKey(upstreamTarget, "upstream")]}
          onClick={() => onExpand(upstreamTarget, "upstream")}
        />
      )}
      {downstreamTarget && (
        <ExpandButton
          direction="downstream"
          loading={loading[loadingKey(downstreamTarget, "downstream")]}
          onClick={() => onExpand(downstreamTarget, "downstream")}
        />
      )}

      <header className="plex-node-header">
        <span className="plex-node-icon" style={{ color, borderColor: `${color}55`, background: `${color}12` }}>
          {iconForNode(node)}
        </span>
        <span className="plex-node-title">
          <small title={cardSubtitle(node)}>
            {compact(cardSubtitle(node))}
          </small>
          <strong title={cardTitle(node)}>{compact(cardTitle(node), 40)}</strong>
        </span>
        <span className="plex-type-pill" style={{ color, background: `${color}12` }}>
          {typeLabelForNode(node)}
        </span>
        <LineageNodeMenu
          hasHighlight={hasHighlight}
          onApplyHighlight={(direction, paletteColor) => onHighlight(node.id, direction, paletteColor)}
          onClearNodeHighlight={() => onClearNodeHighlights(node.id)}
          onClearAllHighlights={onClearAllHighlights}
        />
      </header>

      <div className="plex-node-body">
        <span
          className={`plex-field-line ${highlightColor ? "highlighted" : ""}`}
          style={{ ["--plex-highlight-color" as string]: highlightColor || "transparent" }}
          title={node.technical_name || node.label || node.node_id}
        >
          <strong>{compact(node.label || node.technical_name || node.node_id, 42)}</strong>
          {primaryRole ? <small>{primaryRole}</small> : null}
        </span>
        {!!groupedChildren.length && (
          <div className="plex-grouped-items">
            {groupedChildren.map((child) => (
              <span
                key={child.id}
                className={`plex-grouped-item ${child.highlightColor ? "highlighted" : ""}`}
                style={{ ["--plex-highlight-color" as string]: child.highlightColor || "transparent" }}
                title={child.label}
                onClick={(event) => {
                  event.stopPropagation();
                  if (child.nodeId) onFocus(child.nodeId);
                }}
              >
                <strong>{compact(child.label, 44)}</strong>
                <small>
                  {child.hasUpstream ? "<" : ""}
                  {child.role ? compact(child.role, 18) : ""}
                  {child.hasDownstream ? ">" : ""}
                </small>
              </span>
            ))}
          </div>
        )}
      </div>

      <footer className="plex-node-footer">
        <span className={`plex-continuation ${upstreamTarget ? "available" : ""}`} title="Upstream continuation" />
        <code title={node.node_id}>{compact(node.node_id, 22)}</code>
        <span className={`plex-continuation downstream ${downstreamTarget ? "available" : ""}`} title="Downstream continuation" />
      </footer>
    </article>
  );
}
