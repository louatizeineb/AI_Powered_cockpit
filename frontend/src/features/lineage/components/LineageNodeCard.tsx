import ExpandButton from "./ExpandButton";
import type { HighlightDirection, LineageDirection, LineageNode } from "../types/lineage.types";
import { colorForNode, iconForNode, typeLabelForNode } from "../utils/lineageStyles";
import type { GroupedChildItem } from "../utils/lineageGrouping";
import {
  qualityBadgeForItems,
  qualityTooltip,
  qualityOutcomeForItems,
  type LineageQualityItem,
} from "../utils/lineageQuality";
import LineageNodeMenu from "./LineageNodeMenu";

type LineageNodeCardProps = {
  node: LineageNode;
  focused: boolean;
  focusedNodeId?: string | null;
  highlightColor?: string | null;
  hasHighlight: boolean;
  qualityItems: LineageQualityItem[];
  qualityByNodeId: Record<string, LineageQualityItem[]>;
  groupedChildren: GroupedChildItem[];
  hiddenGroupedChildrenCount: number;
  canShowFewerGroupedChildren: boolean;
  expanded: {
    upstream: Record<string, boolean>;
    downstream: Record<string, boolean>;
  };
  sourceContextExpanded: boolean;
  sourceContextCollapsed: boolean;
  loading: Record<string, boolean>;
  loadingSourceContext: boolean;
  expandedCatalogRows: Record<string, boolean>;
  onFocus: (nodeId: string) => void;
  onExpand: (nodeId: string, direction: LineageDirection) => void;
  onCollapse: (nodeId: string, direction: LineageDirection) => void;
  isLineageCollapsed: (nodeId: string, direction: LineageDirection) => boolean;
  onExpandSourceContext: () => void;
  onToggleSourceContextVisibility: () => void;
  onToggleCatalogRow: (nodeId: string) => void;
  onShowMoreGroupedChildren: () => void;
  onShowFewerGroupedChildren: () => void;
  onHighlight: (nodeId: string, direction: HighlightDirection, color: string) => void;
  onClearNodeHighlights: (nodeId: string) => void;
  onClearAllHighlights: () => void;
  onOpenQualityDetails: (title: string, items: LineageQualityItem[], anchor: DOMRect) => void;
};

function compact(value: string | null | undefined, max = 46) {
  const text = String(value || "");
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

function loadingKey(nodeId: string, direction: LineageDirection) {
  return `${nodeId}:${direction}`;
}

function canExpand(value: unknown) {
  return value === true || value === 1 || String(value).toLowerCase() === "true";
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

function RowExpandButton({
  direction,
  loading,
  expanded,
  onExpand,
}: {
  direction: LineageDirection;
  loading?: boolean;
  expanded?: boolean;
  onExpand: () => void;
}) {
  const action = expanded ? "Collapse" : "Expand";
  return (
    <button
      type="button"
      className={`plex-row-expand plex-row-expand-${direction}`}
      title={`${action} this row ${direction}`}
      disabled={loading}
      onClick={(event) => {
        event.stopPropagation();
        onExpand();
      }}
      aria-label={`${action} this row ${direction}`}
    >
      {loading ? "..." : expanded ? "-" : "+"}
    </button>
  );
}

function QualityPill({
  items,
  row = false,
  title,
  onOpen,
}: {
  items?: LineageQualityItem[];
  row?: boolean;
  title: string;
  onOpen: (title: string, items: LineageQualityItem[], anchor: DOMRect) => void;
}) {
  const qualityItems = items || [];
  const badge = qualityBadgeForItems(qualityItems);
  if (!badge) return null;
  const outcome = qualityOutcomeForItems(qualityItems);
  return (
    <button
      type="button"
      className={`plex-quality-pill ${badge.tone} ${outcome} ${row ? "row" : ""}`}
      title={qualityTooltip(qualityItems) || `${badge.count} quality checks`}
      onClick={(event) => {
        event.stopPropagation();
        onOpen(title, qualityItems, event.currentTarget.getBoundingClientRect());
      }}
    >
      <span className="plex-quality-dot" />
      <strong>{badge.label}</strong>
      {badge.count > 1 ? <em>{badge.count}</em> : null}
    </button>
  );
}

export default function LineageNodeCard({
  node,
  focused,
  focusedNodeId,
  highlightColor,
  hasHighlight,
  qualityItems,
  qualityByNodeId,
  groupedChildren,
  hiddenGroupedChildrenCount,
  canShowFewerGroupedChildren,
  expanded,
  sourceContextExpanded,
  sourceContextCollapsed,
  loading,
  loadingSourceContext,
  expandedCatalogRows,
  onFocus,
  onExpand,
  onCollapse,
  isLineageCollapsed,
  onExpandSourceContext,
  onToggleSourceContextVisibility,
  onToggleCatalogRow,
  onShowMoreGroupedChildren,
  onShowFewerGroupedChildren,
  onHighlight,
  onClearNodeHighlights,
  onClearAllHighlights,
  onOpenQualityDetails,
}: LineageNodeCardProps) {
  const color = colorForNode(node);
  const hasRowExpansion = Boolean(
    groupedChildren.some((child) => child.nodeId && (canExpand(child.hasUpstream) || canExpand(child.hasDownstream))) ||
    (primaryRowRole(node) && (canExpand(node.has_upstream) || canExpand(node.has_downstream)))
  );
  const upstreamTarget = !hasRowExpansion && canExpand(node.has_upstream) ? node.id : undefined;
  const downstreamTarget = !hasRowExpansion && canExpand(node.has_downstream) ? node.id : undefined;
  const primaryRole = primaryRowRole(node);
  const primaryFocused = focusedNodeId === node.id;
  const primaryQuality = qualityByNodeId[node.id] || [];
  const sourceCard = categoryOf(node).includes("source");
  const sourceContextHasMore = node.properties?.source_context_has_more === true;
  const sourceContextLoaded = Number(node.properties?.source_context_next_offset || 0);
  const sourceDescendantCount = Number(node.properties?.children_count || 0);

  return (
    <article
      className={`plex-node-card ${sourceCard ? "source-card" : ""} ${focused ? "focused" : ""} ${highlightColor ? "highlighted" : ""}`}
      style={{ borderLeftColor: color, ["--plex-highlight-color" as string]: highlightColor || "transparent" }}
      onClick={() => onFocus(node.id)}
      tabIndex={0}
    >
      {upstreamTarget && (
        <ExpandButton
          direction="upstream"
          loading={loading[loadingKey(upstreamTarget, "upstream")]}
          expanded={Boolean(expanded.upstream[upstreamTarget] && !isLineageCollapsed(upstreamTarget, "upstream"))}
          onClick={() => expanded.upstream[upstreamTarget] && !isLineageCollapsed(upstreamTarget, "upstream")
            ? onCollapse(upstreamTarget, "upstream")
            : onExpand(upstreamTarget, "upstream")}
        />
      )}
      {downstreamTarget && (
        <ExpandButton
          direction="downstream"
          loading={loading[loadingKey(downstreamTarget, "downstream")]}
          expanded={Boolean(expanded.downstream[downstreamTarget] && !isLineageCollapsed(downstreamTarget, "downstream"))}
          onClick={() => expanded.downstream[downstreamTarget] && !isLineageCollapsed(downstreamTarget, "downstream")
            ? onCollapse(downstreamTarget, "downstream")
            : onExpand(downstreamTarget, "downstream")}
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
        <span className="plex-card-badges">
          <span className="plex-type-pill" style={{ color, background: `${color}12` }}>
            {typeLabelForNode(node)}
          </span>
          <QualityPill
            items={qualityItems}
            title={`${cardTitle(node)} controls`}
            onOpen={onOpenQualityDetails}
          />
        </span>
        <LineageNodeMenu
          hasHighlight={hasHighlight}
          onApplyHighlight={(direction, paletteColor) => onHighlight(node.id, direction, paletteColor)}
          onClearNodeHighlight={() => onClearNodeHighlights(node.id)}
          onClearAllHighlights={onClearAllHighlights}
        />
      </header>

      <div className="plex-node-body">
        {sourceCard && (
          <button
            type="button"
            className="plex-source-context-toggle"
            disabled={loadingSourceContext}
            onClick={(event) => {
              event.stopPropagation();
              if (sourceContextExpanded) onToggleSourceContextVisibility();
              else onExpandSourceContext();
            }}
          >
            {loadingSourceContext ? "Loading catalog..." : sourceContextExpanded && !sourceContextCollapsed ? "Hide structures & fields (-)" : "Show structures & fields (+)"}
          </button>
        )}
        {sourceCard && sourceContextExpanded && !sourceContextCollapsed && sourceContextHasMore && (
          <button
            type="button"
            className="plex-source-context-toggle secondary"
            disabled={loadingSourceContext}
            onClick={(event) => {
              event.stopPropagation();
              onExpandSourceContext();
            }}
          >
            Load more structures & fields
          </button>
        )}
        {sourceCard && sourceContextExpanded && (
          <small className="plex-source-context-summary">
            {sourceContextLoaded.toLocaleString()} catalog rows loaded
            {sourceDescendantCount ? ` / ${sourceDescendantCount.toLocaleString()} reported descendants` : ""}
          </small>
        )}
        <span
          className={`plex-field-line ${highlightColor ? "highlighted" : ""} ${primaryFocused ? "row-focused" : ""}`}
          style={{ ["--plex-highlight-color" as string]: highlightColor || "transparent" }}
          title={node.technical_name || node.label || node.node_id}
          onClick={(event) => {
            if (!event.ctrlKey) return;
            event.stopPropagation();
            onFocus(node.id);
          }}
        >
          <strong>{compact(node.label || node.technical_name || node.node_id, 42)}</strong>
          <span className="plex-row-actions">
            <QualityPill
              items={primaryQuality}
              row
              title={`${node.label || node.technical_name || node.node_id} controls`}
              onOpen={onOpenQualityDetails}
            />
            {canExpand(node.has_upstream) && (
              <RowExpandButton
                direction="upstream"
                loading={loading[loadingKey(node.id, "upstream")]}
                expanded={Boolean(expanded.upstream[node.id] && !isLineageCollapsed(node.id, "upstream"))}
                onExpand={() => expanded.upstream[node.id] && !isLineageCollapsed(node.id, "upstream")
                  ? onCollapse(node.id, "upstream")
                  : onExpand(node.id, "upstream")}
              />
            )}
            {primaryRole ? <small>{primaryRole}</small> : null}
            {canExpand(node.has_downstream) && (
              <RowExpandButton
                direction="downstream"
                loading={loading[loadingKey(node.id, "downstream")]}
                expanded={Boolean(expanded.downstream[node.id] && !isLineageCollapsed(node.id, "downstream"))}
                onExpand={() => expanded.downstream[node.id] && !isLineageCollapsed(node.id, "downstream")
                  ? onCollapse(node.id, "downstream")
                  : onExpand(node.id, "downstream")}
              />
            )}
          </span>
        </span>
        {!!groupedChildren.length && (
          <div className="plex-grouped-items">
            {groupedChildren.map((child) => {
              const childNodeId = child.nodeId || "";
              const childQuality = childNodeId ? qualityByNodeId[childNodeId] || [] : [];
              const semanticRow = child.role === "Field" || child.role === "DPI" || child.role === "Usage";
              const canExpandUpstream = Boolean(semanticRow && childNodeId && canExpand(child.hasUpstream));
              const canExpandDownstream = Boolean(semanticRow && childNodeId && canExpand(child.hasDownstream));
              const hasCatalogChildren = Boolean(child.catalog && child.children?.length);
              const catalogExpanded = Boolean(childNodeId && expandedCatalogRows[childNodeId]);
              return (
                <span
                  key={child.id}
                  className={`plex-grouped-item ${child.highlightColor ? "highlighted" : ""} ${focusedNodeId === child.nodeId ? "row-focused" : ""}`}
                  style={{
                    ["--plex-highlight-color" as string]: child.highlightColor || "transparent",
                    ["--plex-row-depth" as string]: child.depth || 0,
                  }}
                  title={[child.label, ...(child.linkedTo || [])].join("\n")}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (child.nodeId) onFocus(child.nodeId);
                    if (child.nodeId && hasCatalogChildren) onToggleCatalogRow(child.nodeId);
                  }}
                >
                  {hasCatalogChildren ? (
                    <button
                      type="button"
                      className="plex-catalog-disclosure"
                      aria-label={catalogExpanded ? `Collapse ${child.label}` : `Expand ${child.label}`}
                      title={catalogExpanded ? "Hide contained rows" : "Show contained rows"}
                      onClick={(event) => {
                        event.stopPropagation();
                        if (childNodeId) onToggleCatalogRow(childNodeId);
                      }}
                    >
                      {catalogExpanded ? "-" : "+"}
                    </button>
                  ) : <span className="plex-catalog-disclosure-spacer" />}
                  <span className="plex-grouped-copy">
                    <strong>{compact(child.label, 44)}</strong>
                  </span>
                  <span className="plex-row-actions">
                    <QualityPill
                      items={childQuality}
                      row
                      title={`${child.label} controls`}
                      onOpen={onOpenQualityDetails}
                    />
                    {canExpandUpstream && (
                      <RowExpandButton
                        direction="upstream"
                        loading={loading[loadingKey(childNodeId, "upstream")]}
                        expanded={Boolean(expanded.upstream[childNodeId] && !isLineageCollapsed(childNodeId, "upstream"))}
                        onExpand={() => expanded.upstream[childNodeId] && !isLineageCollapsed(childNodeId, "upstream")
                          ? onCollapse(childNodeId, "upstream")
                          : onExpand(childNodeId, "upstream")}
                      />
                    )}
                    {child.role ? <small title={child.section ? `${child.role} / ${child.section}` : child.role}>{compact(child.role, 14)}</small> : null}
                    {canExpandDownstream && (
                      <RowExpandButton
                        direction="downstream"
                        loading={loading[loadingKey(childNodeId, "downstream")]}
                        expanded={Boolean(expanded.downstream[childNodeId] && !isLineageCollapsed(childNodeId, "downstream"))}
                        onExpand={() => expanded.downstream[childNodeId] && !isLineageCollapsed(childNodeId, "downstream")
                          ? onCollapse(childNodeId, "downstream")
                          : onExpand(childNodeId, "downstream")}
                      />
                    )}
                  </span>
                </span>
              );
            })}
          </div>
        )}
        {(hiddenGroupedChildrenCount > 0 || canShowFewerGroupedChildren) && (
          <div className="plex-card-row-controls">
            {hiddenGroupedChildrenCount > 0 && (
              <button type="button" className="plex-card-show-more" onClick={onShowMoreGroupedChildren}>
                Show more
              </button>
            )}
            {canShowFewerGroupedChildren && (
              <button type="button" className="plex-card-show-more plex-card-show-less" onClick={onShowFewerGroupedChildren}>
                Show less
              </button>
            )}
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
