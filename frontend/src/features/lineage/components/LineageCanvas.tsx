import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type WheelEvent as ReactWheelEvent,
} from "react";
import LineageNodeCard from "./LineageNodeCard";
import FullscreenToggle from "./FullscreenToggle";
import type {
  HighlightDirection,
  LineageDirection,
  LineageEdge,
  LineageGraphState,
  HighlightedPath,
  LineagePosition,
} from "../types/lineage.types";
import {
  BOARD_PADDING,
  boardBounds,
  CARD_HEIGHT,
  CARD_WIDTH,
  toBoardPosition,
} from "../utils/lineageLayout";
import {
  buildGroupingFromGraph,
  visibleCatalogRowByNodeId,
  visibleGroupedChildren,
  type GroupedChildItem,
} from "../utils/lineageGrouping";
import { filterDpiStoryEdges } from "../utils/lineageBranchFilter";
import { canonicalRelType } from "../utils/lineageStageClassifier";
import {
  mergeQualityItems,
  qualityControlName,
  qualityControlTarget,
  qualityCountLabel,
  qualityOutcomeForItems,
  qualityScoreLabel,
  qualityStatusLabel,
  qualityText,
  statusScoreLabel,
  usageQualityScoreLabel,
  type LineageQualityItem,
} from "../utils/lineageQuality";

type LineageCanvasProps = {
  graph: LineageGraphState;
  positions: Record<string, LineagePosition>;
  qualityByNodeId: Record<string, LineageQualityItem[]>;
  loading: Record<string, boolean>;
  loadingSourceContexts: Record<string, boolean>;
  onFocus: (nodeId: string) => void;
  onMoveNode: (nodeId: string, position: LineagePosition) => void;
  onExpand: (nodeId: string, direction: LineageDirection) => void;
  onExpandSourceContext: (nodeId: string) => void;
  onHighlight: (nodeId: string, direction: HighlightDirection, color: string) => void;
  onClearNodeHighlights: (nodeId: string) => void;
  onClearAllHighlights: () => void;
  onResetLayout: () => void;
};

const CANVAS_RUNWAY_X = 360;
const CANVAS_RUNWAY_Y = 420;
const PRIMARY_ROW_CENTER_Y = 73;
const GROUPED_ROW_START_Y = 98;
const GROUPED_ROW_STEP_Y = 30;
const CARD_LAYOUT_WIDTH = 430;
const INITIAL_VISIBLE_CARD_ROWS = 6;
const CARD_ROW_REVEAL_STEP = 8;
const EXPANSION_ZOOM_STEP = 0.04;
const BUSINESS_TERM_EDGE_COLOR = "#00A6C7";

type GroupingState = ReturnType<typeof buildGroupingFromGraph>;
type VisualCanvasEdge = LineageEdge & {
  sourceRowId?: string;
  targetRowId?: string;
};

type QualityPanelState = {
  title: string;
  items: LineageQualityItem[];
  left: number;
  top: number;
} | null;

function lineageBranchKey(nodeId: string, direction: LineageDirection) {
  return `${nodeId}:${direction}`;
}

function isContextEdge(edge: LineageEdge) {
  const type = canonicalRelType(edge.type || edge.raw_type);
  return (
    type.includes("PART_OF") ||
    type.includes("PROCESSING_CONTEXT") ||
    type.includes("HAS_FIELD") ||
    type.includes("HAS_COLUMN") ||
    type.includes("HAS_STRUCTURE") ||
    type.includes("HAS_CONTAINER") ||
    type.includes("CONTAINS")
  );
}

function collapsedLineageNodeIds(graph: LineageGraphState, collapsedBranches: Record<string, boolean>) {
  const hidden = new Set<string>();
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const visualEdges = graph.edges.map((edge) => ({
    edge,
    source: edge.visual_source || edge.source,
    target: edge.visual_target || edge.target,
  }));

  Object.entries(collapsedBranches).forEach(([key, collapsed]) => {
    if (!collapsed) return;
    const direction = key.endsWith(":upstream") ? "upstream" : "downstream";
    const root = key.slice(0, -(direction.length + 1));
    const branchHidden = new Set<string>();
    const queue = [root];

    while (queue.length) {
      const current = queue.shift()!;
      visualEdges.forEach(({ edge, source, target }) => {
        if (isContextEdge(edge)) return;
        const next = direction === "downstream" && source === current
          ? target
          : direction === "upstream" && target === current
            ? source
            : undefined;
        if (!next || next === root || branchHidden.has(next)) return;
        branchHidden.add(next);
        queue.push(next);
      });
    }

    let addedContext = true;
    while (addedContext) {
      addedContext = false;
      visualEdges.forEach(({ edge, source, target }) => {
        if (!isContextEdge(edge)) return;
        if (!branchHidden.has(target) || source === root || branchHidden.has(source)) return;
        if (String(nodesById.get(source)?.category || "").toLowerCase() === "source") return;
        branchHidden.add(source);
        addedContext = true;
      });
    }

    branchHidden.forEach((id) => hidden.add(id));
  });

  return hidden;
}

function isBusinessTermNode(node?: { type?: string; category?: string; entity_type?: string | null; data_type?: string | null; properties?: Record<string, unknown> }) {
  if (!node) return false;
  const text = [
    node.type,
    node.category,
    node.entity_type,
    node.data_type,
    node.properties?.labels,
    node.properties?.entity_type,
    node.properties?.data_type,
  ].flat().filter(Boolean).join(" ").replace(/[_-]/g, " ").toLowerCase();
  return text.includes("business term") || text.includes("businessterm") || text.includes("glossary term");
}

function isBusinessTermEdge(edge: LineageEdge, nodesById: Map<string, LineageGraphState["nodes"][number]>) {
  const type = canonicalRelType(edge.type || edge.raw_type).replace(/_/g, " ").toLowerCase();
  return type.includes("business term") ||
    isBusinessTermNode(nodesById.get(edge.visual_source || edge.source)) ||
    isBusinessTermNode(nodesById.get(edge.visual_target || edge.target));
}

function rowAnchorY(cardId: string, rowId: string | undefined, rowsByParentId: Record<string, GroupedChildItem[]>) {
  if (!rowId || rowId === cardId) return PRIMARY_ROW_CENTER_Y;
  const rows = rowsByParentId[cardId] || [];
  const index = rows.findIndex((row) => row.nodeId === rowId);
  if (index >= 0) return GROUPED_ROW_START_Y + index * GROUPED_ROW_STEP_Y;
  return PRIMARY_ROW_CENTER_Y;
}

function edgePath(edge: VisualCanvasEdge, positions: Record<string, LineagePosition>, rowsByParentId: Record<string, GroupedChildItem[]>) {
  const source = positions[edge.source];
  const target = positions[edge.target];
  if (!source || !target) return "";

  const sourceOffsetY = rowAnchorY(edge.source, edge.sourceRowId, rowsByParentId);
  const targetOffsetY = rowAnchorY(edge.target, edge.targetRowId, rowsByParentId);
  const sourceIsLeft = source.x <= target.x;

  // Last-resort visual safety: if a bad stage slips through, the line still reads left -> right.
  const startCard = sourceIsLeft ? source : target;
  const endCard = sourceIsLeft ? target : source;
  const startOffsetY = sourceIsLeft ? sourceOffsetY : targetOffsetY;
  const endOffsetY = sourceIsLeft ? targetOffsetY : sourceOffsetY;
  const startX = startCard.x + CARD_WIDTH;
  const startY = startCard.y + startOffsetY;
  const endX = endCard.x;
  const endY = endCard.y + endOffsetY;
  const curve = Math.max(92, Math.abs(endX - startX) * 0.46);
  const c1x = startX + curve;
  const c2x = endX - curve;
  return `M ${startX} ${startY} C ${c1x} ${startY}, ${c2x} ${endY}, ${endX} ${endY}`;
}

function edgeHighlightColor(edgeId: string, highlights: HighlightedPath[]): string | null {
  const hit = [...highlights].reverse().find((highlight) => highlight.edgeIds.includes(edgeId));
  return hit?.color || null;
}

function nodeHighlightColor(nodeId: string, highlights: HighlightedPath[]): string | null {
  const hit = [...highlights].reverse().find((highlight) => highlight.nodeIds.includes(nodeId));
  return hit?.color || null;
}

function visualEdgeKey(edge: LineageEdge, source: string, target: string) {
  return `${source}->${target}:${edge.visual_source || edge.source}:${edge.visual_target || edge.target}:${edge.type}`;
}

function graphBox(positions: Record<string, LineagePosition>, cardHeights: Record<string, number>) {
  const values = Object.values(positions);
  if (!values.length) return { width: 900, height: 520 };
  const minX = Math.min(...values.map((position) => position.x));
  const minY = Math.min(...values.map((position) => position.y));
  const maxX = Math.max(...values.map((position) => position.x + CARD_WIDTH));
  const maxY = Math.max(...Object.entries(positions).map(([id, position]) => position.y + (cardHeights[id] || CARD_HEIGHT)));
  return {
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
  };
}

function estimatedCardHeight(rows: GroupedChildItem[], sourceCard: boolean, hasRowControls: boolean) {
  return CARD_HEIGHT + rows.length * 30 + (sourceCard ? 82 : 0) + (hasRowControls ? 32 : 0);
}

function collisionAwarePositions(
  positions: Record<string, LineagePosition>,
  graph: LineageGraphState,
  grouping: GroupingState,
  cardHeights: Record<string, number>
) {
  const next: Record<string, LineagePosition> = {};
  graph.nodes.forEach((node) => {
    if (positions[node.id]) next[node.id] = positions[node.id];
  });
  const visibleIds = graph.nodes
    .filter((node) => !grouping.hiddenNodeIds[node.id] && positions[node.id])
    .map((node) => node.id)
    .sort((a, b) => positions[a].x - positions[b].x || positions[a].y - positions[b].y);
  const placed: string[] = [];
  visibleIds.forEach((id) => {
    const position = next[id];
    const height = cardHeights[id] || CARD_HEIGHT;
    let y = position.y;
    let adjusted = true;
    while (adjusted) {
      adjusted = false;
      placed.forEach((otherId) => {
        const other = next[otherId];
        const overlapsHorizontally =
          position.x < other.x + CARD_LAYOUT_WIDTH &&
          position.x + CARD_LAYOUT_WIDTH > other.x;
        const overlapsVertically =
          y < other.y + (cardHeights[otherId] || CARD_HEIGHT) + 28 &&
          y + height + 28 > other.y;
        if (overlapsHorizontally && overlapsVertically) {
          y = other.y + (cardHeights[otherId] || CARD_HEIGHT) + 28;
          adjusted = true;
        }
      });
    }
    next[id] = { ...position, y };
    placed.push(id);
  });
  graph.nodes.forEach((node) => {
    const parent = grouping.parentByChildId[node.id];
    if (parent && next[parent]) next[node.id] = { ...next[parent] };
  });
  return next;
}

export default function LineageCanvas({
  graph,
  positions,
  qualityByNodeId,
  loading,
  loadingSourceContexts,
  onFocus,
  onMoveNode,
  onExpand,
  onExpandSourceContext,
  onHighlight,
  onClearNodeHighlights,
  onClearAllHighlights,
}: LineageCanvasProps) {
  const [zoom, setZoom] = useState(0.9);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isPanning, setIsPanning] = useState(false);
  const [storyBranchesOnly, setStoryBranchesOnly] = useState(true);
  const [expandedCatalogRows, setExpandedCatalogRows] = useState<Record<string, boolean>>({});
  const [visibleCatalogRowCounts, setVisibleCatalogRowCounts] = useState<Record<string, number>>({});
  const [collapsedLineageBranches, setCollapsedLineageBranches] = useState<Record<string, boolean>>({});
  const [collapsedSourceContexts, setCollapsedSourceContexts] = useState<Record<string, boolean>>({});
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null);
  const [qualityPanel, setQualityPanel] = useState<QualityPanelState>(null);
  const [measuredCardHeights, setMeasuredCardHeights] = useState<Record<string, number>>({});
  const shellRef = useRef<HTMLDivElement | null>(null);
  const qualityPanelRef = useRef<HTMLElement | null>(null);
  const nodePositionRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const lastNodeCountRef = useRef(0);
  const dragRef = useRef<{
    nodeId: string;
    startX: number;
    startY: number;
    origin: LineagePosition;
    moved: boolean;
  } | null>(null);
  const panRef = useRef<{
    startX: number;
    startY: number;
    scrollLeft: number;
    scrollTop: number;
  } | null>(null);
  const highlightByNodeId = useMemo(() => {
    const mapping: Record<string, string | null> = {};
    graph.highlights.forEach((highlight) => {
      highlight.nodeIds.forEach((nodeId) => {
        mapping[nodeId] = highlight.color;
      });
    });
    return mapping;
  }, [graph.highlights]);
  const collapsedNodeIds = useMemo(
    () => collapsedLineageNodeIds(graph, collapsedLineageBranches),
    [collapsedLineageBranches, graph]
  );
  const canvasGraph = useMemo(() => {
    const nodes = graph.nodes.filter((node) => !collapsedNodeIds.has(node.id));
    const nodeIds = new Set(nodes.map((node) => node.id));
    return {
      ...graph,
      nodes,
      edges: graph.edges.filter((edge) => {
        const source = edge.visual_source || edge.source;
        const target = edge.visual_target || edge.target;
        return nodeIds.has(source) && nodeIds.has(target);
      }),
    };
  }, [collapsedNodeIds, graph]);
  const grouping = useMemo(
    () => buildGroupingFromGraph(canvasGraph.nodes, canvasGraph.edges, highlightByNodeId),
    [canvasGraph.edges, canvasGraph.nodes, highlightByNodeId]
  );
  const availableRowsByParentId = useMemo(() => {
    const mapping: Record<string, GroupedChildItem[]> = {};
    Object.entries(grouping.groupedByParentId).forEach(([parentId, rows]) => {
      mapping[parentId] = collapsedSourceContexts[parentId] ? [] : visibleGroupedChildren(rows, expandedCatalogRows);
    });
    return mapping;
  }, [collapsedSourceContexts, expandedCatalogRows, grouping.groupedByParentId]);
  const visibleRowsByParentId = useMemo(() => {
    const mapping: Record<string, GroupedChildItem[]> = {};
    Object.entries(availableRowsByParentId).forEach(([parentId, rows]) => {
      mapping[parentId] = rows.slice(0, visibleCatalogRowCounts[parentId] || INITIAL_VISIBLE_CARD_ROWS);
    });
    return mapping;
  }, [availableRowsByParentId, visibleCatalogRowCounts]);
  const visibleCatalogRowOwner = useMemo(() => {
    const mapping: Record<string, string> = {};
    Object.values(grouping.groupedByParentId).forEach((rows) => {
      Object.assign(mapping, visibleCatalogRowByNodeId(rows, expandedCatalogRows));
    });
    return mapping;
  }, [expandedCatalogRows, grouping.groupedByParentId]);
  const cardHeights = useMemo(() => {
    const mapping: Record<string, number> = {};
    canvasGraph.nodes.forEach((node) => {
      if (grouping.hiddenNodeIds[node.id]) return;
      const availableRows = availableRowsByParentId[node.id] || [];
      const visibleRows = visibleRowsByParentId[node.id] || [];
      mapping[node.id] = estimatedCardHeight(
        visibleRows,
        String(node.category).toLowerCase() === "source",
        availableRows.length > visibleRows.length || visibleRows.length > INITIAL_VISIBLE_CARD_ROWS
      );
      mapping[node.id] = Math.max(mapping[node.id], measuredCardHeights[node.id] || 0);
    });
    return mapping;
  }, [availableRowsByParentId, canvasGraph.nodes, grouping.hiddenNodeIds, measuredCardHeights, visibleRowsByParentId]);
  const displayPositions = useMemo(
    () => collisionAwarePositions(positions, canvasGraph, grouping, cardHeights),
    [canvasGraph, cardHeights, grouping, positions]
  );
  const bounds = useMemo(() => boardBounds(displayPositions, cardHeights), [cardHeights, displayPositions]);
  const contentBox = useMemo(() => graphBox(displayPositions, cardHeights), [cardHeights, displayPositions]);
  const boardSize = useMemo(
    () => ({
      width: bounds.width + CANVAS_RUNWAY_X,
      height: bounds.height + CANVAS_RUNWAY_Y,
    }),
    [bounds.height, bounds.width]
  );
  const boardPositions = useMemo(() => {
    const next: Record<string, LineagePosition> = {};
    Object.entries(displayPositions).forEach(([id, position]) => {
      next[id] = toBoardPosition(position, bounds);
    });
    return next;
  }, [bounds, displayPositions]);
  useEffect(() => {
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver((entries) => {
      setMeasuredCardHeights((current) => {
        const next = { ...current };
        let changed = false;
        entries.forEach((entry) => {
          const nodeId = (entry.target as HTMLElement).dataset.nodeId;
          const height = Math.ceil(entry.contentRect.height);
          if (!nodeId || height <= 0 || next[nodeId] === height) return;
          next[nodeId] = height;
          changed = true;
        });
        return changed ? next : current;
      });
    });
    Object.values(nodePositionRefs.current).forEach((element) => {
      if (element) observer.observe(element);
    });
    return () => observer.disconnect();
  }, [canvasGraph.nodes, visibleRowsByParentId]);
  const visualEdges = useMemo(() => {
    const mapped = new Map<string, VisualCanvasEdge>();
    const branchEdges = storyBranchesOnly
      ? filterDpiStoryEdges(canvasGraph.nodes, canvasGraph.edges, grouping)
      : canvasGraph.edges;
    branchEdges.forEach((edge) => {
      const visualSource = edge.visual_source || edge.source;
      const visualTarget = edge.visual_target || edge.target;
      const source = grouping.parentByChildId[visualSource] || visualSource;
      const target = grouping.parentByChildId[visualTarget] || visualTarget;
      if (source === target) return;
      const key = visualEdgeKey(edge, source, target);
      const existing = mapped.get(key);
      if (!existing || (!edgeHighlightColor(existing.id, graph.highlights) && edgeHighlightColor(edge.id, graph.highlights))) {
        mapped.set(key, {
          ...edge,
          source,
          target,
          sourceRowId: visibleCatalogRowOwner[visualSource] || visualSource,
          targetRowId: visibleCatalogRowOwner[visualTarget] || visualTarget,
        });
      }
    });
    return [...mapped.values()] as VisualCanvasEdge[];
  }, [canvasGraph.edges, canvasGraph.nodes, graph.highlights, grouping, storyBranchesOnly, visibleCatalogRowOwner]);
  const canvasNodesById = useMemo(
    () => new Map(canvasGraph.nodes.map((node) => [node.id, node])),
    [canvasGraph.nodes]
  );

  function cardHighlightColor(nodeId: string) {
    const direct = nodeHighlightColor(nodeId, graph.highlights);
    if (direct) return direct;
    const groupedRows = grouping.groupedByParentId[nodeId] || [];
    return groupedRows.find((row) => row.highlightColor)?.highlightColor || null;
  }

  function centerBoard(nextZoom = zoom) {
    const shell = shellRef.current;
    if (!shell) return;
    shell.scrollLeft = Math.max(0, (boardSize.width * nextZoom - shell.clientWidth) / 2);
    shell.scrollTop = Math.max(0, (boardSize.height * nextZoom - shell.clientHeight) / 2);
  }

  function fitToGraph(maxZoom = 1.05) {
    const shell = shellRef.current;
    if (!shell) return;
    const fitX = (shell.clientWidth - 160) / Math.max(contentBox.width + BOARD_PADDING * 0.6, 1);
    const fitY = (shell.clientHeight - 150) / Math.max(contentBox.height + BOARD_PADDING * 0.5, 1);
    const nextZoom = Math.max(0.5, Math.min(maxZoom, fitX, fitY));
    setZoom(nextZoom);
    requestAnimationFrame(() => {
      centerBoard(nextZoom);
    });
  }

  useEffect(() => {
    if (graph.nodes.length === 1 && lastNodeCountRef.current !== 1) {
      requestAnimationFrame(() => fitToGraph(0.92));
    }
    lastNodeCountRef.current = graph.nodes.length;
  }, [graph.nodes.length]);

  function zoomOutForExpansion() {
    setZoom((value) => Math.max(0.45, Number((value - EXPANSION_ZOOM_STEP).toFixed(2))));
  }

  function handleExpand(nodeId: string, direction: LineageDirection) {
    const key = lineageBranchKey(nodeId, direction);
    if (collapsedLineageBranches[key]) {
      zoomOutForExpansion();
      setCollapsedLineageBranches((current) => ({ ...current, [key]: false }));
      return;
    }
    zoomOutForExpansion();
    onExpand(nodeId, direction);
  }

  function handleCollapse(nodeId: string, direction: LineageDirection) {
    setCollapsedLineageBranches((current) => ({
      ...current,
      [lineageBranchKey(nodeId, direction)]: true,
    }));
  }

  function isLineageCollapsed(nodeId: string, direction: LineageDirection) {
    return Boolean(collapsedLineageBranches[lineageBranchKey(nodeId, direction)]);
  }

  function handleExpandSourceContext(nodeId: string) {
    zoomOutForExpansion();
    onExpandSourceContext(nodeId);
  }

  function toggleSourceContextVisibility(nodeId: string) {
    setCollapsedSourceContexts((current) => ({
      ...current,
      [nodeId]: !current[nodeId],
    }));
  }

  function toggleCatalogRow(rowId: string) {
    if (!expandedCatalogRows[rowId]) zoomOutForExpansion();
    setExpandedCatalogRows((current) => ({
      ...current,
      [rowId]: !current[rowId],
    }));
  }

  function showMoreCatalogRows(nodeId: string) {
    setVisibleCatalogRowCounts((current) => ({
      ...current,
      [nodeId]: (current[nodeId] || INITIAL_VISIBLE_CARD_ROWS) + CARD_ROW_REVEAL_STEP,
    }));
  }

  function showFewerCatalogRows(nodeId: string) {
    setVisibleCatalogRowCounts((current) => ({
      ...current,
      [nodeId]: INITIAL_VISIBLE_CARD_ROWS,
    }));
  }

  useEffect(() => {
    function handleMove(event: MouseEvent) {
      if (dragRef.current) {
        event.preventDefault();
        const next = {
          x: dragRef.current.origin.x + (event.clientX - dragRef.current.startX) / Math.max(zoom, 0.1),
          y: dragRef.current.origin.y + (event.clientY - dragRef.current.startY) / Math.max(zoom, 0.1),
        };
        dragRef.current.moved = true;
        onMoveNode(dragRef.current.nodeId, next);
      }
      if (panRef.current && shellRef.current) {
        event.preventDefault();
        shellRef.current.scrollLeft = panRef.current.scrollLeft - (event.clientX - panRef.current.startX);
        shellRef.current.scrollTop = panRef.current.scrollTop - (event.clientY - panRef.current.startY);
      }
    }

    function handleUp() {
      dragRef.current = null;
      panRef.current = null;
      setDraggingNodeId(null);
      setIsPanning(false);
    }

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [onMoveNode, zoom]);

  useEffect(() => {
    function handleFullscreenChange() {
      setIsFullscreen(document.fullscreenElement === shellRef.current);
    }

    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);

  useEffect(() => {
    function handleOutsideClick(event: MouseEvent) {
      if (!qualityPanel || !qualityPanelRef.current) return;
      if (!qualityPanelRef.current.contains(event.target as Node)) setQualityPanel(null);
    }

    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [qualityPanel]);

  async function toggleFullscreen() {
    const shell = shellRef.current;
    if (!shell) return;
    if (document.fullscreenElement === shell) {
      await document.exitFullscreen();
      return;
    }
    await shell.requestFullscreen();
    requestAnimationFrame(() => fitToGraph(1.08));
  }

  function startPan(event: ReactMouseEvent<HTMLElement>) {
    if (event.button !== 2 || !shellRef.current) return;
    event.preventDefault();
    panRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: shellRef.current.scrollLeft,
      scrollTop: shellRef.current.scrollTop,
    };
    setIsPanning(true);
  }

  function handleCanvasMouseDown(event: ReactMouseEvent<HTMLElement>) {
    startPan(event);
  }

  function handleCanvasWheel(event: ReactWheelEvent<HTMLElement>) {
    if (!event.ctrlKey && !event.metaKey) return;
    const shell = shellRef.current;
    if (!shell) return;
    event.preventDefault();
    const rect = shell.getBoundingClientRect();
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;
    const contentX = (shell.scrollLeft + pointerX) / zoom;
    const contentY = (shell.scrollTop + pointerY) / zoom;
    const factor = event.deltaY < 0 ? 1.08 : 0.92;
    const nextZoom = Math.max(0.45, Math.min(1.4, Number((zoom * factor).toFixed(3))));
    setZoom(nextZoom);
    requestAnimationFrame(() => {
      shell.scrollLeft = Math.max(0, contentX * nextZoom - pointerX);
      shell.scrollTop = Math.max(0, contentY * nextZoom - pointerY);
    });
  }

  function openQualityPanel(title: string, items: LineageQualityItem[], anchor: DOMRect) {
    const panelWidth = Math.min(560, window.innerWidth - 24);
    const placeRight = anchor.right + 12 + panelWidth <= window.innerWidth;
    const left = placeRight
      ? anchor.right + 12
      : Math.max(12, anchor.left - panelWidth - 12);
    const top = Math.max(12, Math.min(anchor.top, window.innerHeight - 380));
    setQualityPanel({ title, items, left, top });
  }

  function startNodeDrag(event: ReactMouseEvent<HTMLDivElement>, nodeId: string) {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest("button, .plex-quality-panel")) return;
    const raw = positions[nodeId];
    if (!raw) return;
    dragRef.current = {
      nodeId,
      startX: event.clientX,
      startY: event.clientY,
      origin: raw,
      moved: false,
    };
    setDraggingNodeId(nodeId);
  }

  if (!graph.nodes.length) {
    return (
      <section className="plex-canvas empty">
        <div className="plex-empty">
          <strong>Search to start the lineage story</strong>
          <span>The first result appears alone, then grows only when you expand it.</span>
        </div>
      </section>
    );
  }

  return (
    <section
      className={`plex-canvas ${isPanning ? "panning" : ""}`}
      ref={shellRef}
      onMouseDown={handleCanvasMouseDown}
      onWheel={handleCanvasWheel}
      onContextMenu={(event) => event.preventDefault()}
    >
      <div className="plex-canvas-controls">
        <button
          type="button"
          className={storyBranchesOnly ? "active" : ""}
          onClick={() => setStoryBranchesOnly((value) => !value)}
          title="Hide direct ID to ID shortcut edges when a DPI/DP bridge exists"
        >
          DPI chain
        </button>
        <button type="button" className="plex-icon-button" onClick={() => fitToGraph(1.05)} title="Fit view" aria-label="Fit view">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M12 3v4" />
            <path d="M12 17v4" />
            <path d="M3 12h4" />
            <path d="M17 12h4" />
          </svg>
        </button>
        <button type="button" className="plex-icon-button" onClick={onClearAllHighlights} title="Clear highlights" aria-label="Clear highlights">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M3 6h18" />
            <path d="M8 6V4h8v2" />
            <path d="M9 10v8" />
            <path d="M15 10v8" />
            <path d="M6 6l1 15h10l1-15" />
          </svg>
        </button>
        <FullscreenToggle active={isFullscreen} onToggle={toggleFullscreen} />
        <button type="button" className="plex-icon-button" onClick={() => setZoom((value) => Math.max(0.45, value - 0.1))} title="Zoom out" aria-label="Zoom out">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 12h14" />
          </svg>
        </button>
        <strong>{Math.round(zoom * 100)}%</strong>
        <button type="button" className="plex-icon-button" onClick={() => setZoom((value) => Math.min(1.4, value + 0.1))} title="Zoom in" aria-label="Zoom in">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14" />
            <path d="M5 12h14" />
          </svg>
        </button>
      </div>
      {qualityPanel && (
        <aside
          className="plex-quality-panel"
          ref={qualityPanelRef}
          style={{ left: qualityPanel.left, top: qualityPanel.top }}
          onMouseDown={(event) => event.stopPropagation()}
        >
          <header>
            <span>
              <small>Control checks</small>
              <strong>{qualityPanel.title}</strong>
            </span>
            <button type="button" onClick={() => setQualityPanel(null)} title="Close control details">Close</button>
          </header>
          <div className="plex-quality-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Control</th>
                  <th>Target</th>
                  <th>Usage score</th>
                  <th>Status score</th>
                  <th>Control score</th>
                  <th>Counts</th>
                  <th>Tool</th>
                </tr>
              </thead>
              <tbody>
                {qualityPanel.items.map((item, index) => {
                  const outcome = qualityOutcomeForItems([item]);
                  return (
                    <tr key={`${qualityText(item.id || item.check_id || item.resolved_id, "control")}-${index}`}>
                      <td>
                        <span className={`plex-quality-status ${outcome}`}>
                          {qualityStatusLabel(item)}
                        </span>
                        <small>{qualityText(item.control_status || item.usage_quality_status || item.quality_status || item.status, "")}</small>
                      </td>
                      <td>
                        <strong>{qualityControlName(item)}</strong>
                        <small>{qualityText(item.quality_dimension || item.control_link, "")}</small>
                      </td>
                      <td>{qualityControlTarget(item)}</td>
                      <td>{usageQualityScoreLabel(item)}</td>
                      <td>{statusScoreLabel(item)}</td>
                      <td>{qualityScoreLabel(item)}</td>
                      <td>{qualityCountLabel(item)}</td>
                      <td>{qualityText(item.control_tool || item.__dqc_backend || item.__legacy_quality_result, "-")}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </aside>
      )}
      <div
        className="plex-board"
        style={{
          width: boardSize.width,
          height: boardSize.height,
          transform: `scale(${zoom})`,
        }}
      >
        <svg className="plex-edges" width={boardSize.width} height={boardSize.height}>
          <defs>
            <marker id="plex-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
              <path d="M0,0 L9,4.5 L0,9 Z" />
            </marker>
            <marker id="plex-arrow-business-term" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
              <path d="M0,0 L9,4.5 L0,9 Z" />
            </marker>
          </defs>
          {visualEdges.map((edge) => {
            const businessTerm = isBusinessTermEdge(edge, canvasNodesById);
            return (
              <path
                key={edge.id}
                className={`plex-edge ${businessTerm ? "business-term" : ""} ${edgeHighlightColor(edge.id, graph.highlights) ? "highlighted" : ""}`}
                style={{
                  ["--plex-edge-color" as string]: businessTerm ? BUSINESS_TERM_EDGE_COLOR : "#7da2ff",
                  ["--plex-edge-highlight" as string]: edgeHighlightColor(edge.id, graph.highlights) || (businessTerm ? BUSINESS_TERM_EDGE_COLOR : "#7da2ff"),
                  markerEnd: businessTerm ? 'url("#plex-arrow-business-term")' : 'url("#plex-arrow")',
                } as CSSProperties}
                d={edgePath(edge, boardPositions, visibleRowsByParentId)}
              >
                <title>{edge.type}</title>
              </path>
            );
          })}
        </svg>

        {canvasGraph.nodes.map((node) => {
          if (grouping.hiddenNodeIds[node.id]) return null;
          const position = boardPositions[node.id];
          if (!position) return null;
          const groupedChildren = visibleRowsByParentId[node.id] || [];
          const hiddenGroupedChildrenCount = Math.max(
            0,
            (availableRowsByParentId[node.id] || []).length - groupedChildren.length
          );
          const qualityItems = mergeQualityItems([
            qualityByNodeId[node.id],
            ...groupedChildren.map((child) => (child.nodeId ? qualityByNodeId[child.nodeId] : [])),
          ]);
          return (
            <div
              key={node.id}
              ref={(element) => {
                if (element) nodePositionRefs.current[node.id] = element;
                else delete nodePositionRefs.current[node.id];
              }}
              data-node-id={node.id}
              className={`plex-node-position ${draggingNodeId === node.id ? "dragging" : ""}`}
              style={{ left: position.x, top: position.y, minWidth: CARD_WIDTH, width: "max-content", maxWidth: 430 }}
              onMouseDown={(event) => startNodeDrag(event, node.id)}
            >
              <LineageNodeCard
                node={node}
                focused={graph.focusedNodeId === node.id}
                focusedNodeId={graph.focusedNodeId}
                highlightColor={cardHighlightColor(node.id)}
                hasHighlight={graph.highlights.some((item) => item.sourceNodeId === node.id)}
                qualityItems={qualityItems}
                qualityByNodeId={qualityByNodeId}
                groupedChildren={groupedChildren}
                hiddenGroupedChildrenCount={hiddenGroupedChildrenCount}
                canShowFewerGroupedChildren={groupedChildren.length > INITIAL_VISIBLE_CARD_ROWS}
                expanded={graph.expanded}
                sourceContextExpanded={Boolean(graph.sourceContextExpanded[node.id])}
                sourceContextCollapsed={Boolean(collapsedSourceContexts[node.id])}
                loading={loading}
                loadingSourceContext={Boolean(loadingSourceContexts[node.id])}
                expandedCatalogRows={expandedCatalogRows}
                onFocus={onFocus}
                onExpand={handleExpand}
                onCollapse={handleCollapse}
                isLineageCollapsed={isLineageCollapsed}
                onExpandSourceContext={() => handleExpandSourceContext(node.id)}
                onToggleSourceContextVisibility={() => toggleSourceContextVisibility(node.id)}
                onToggleCatalogRow={toggleCatalogRow}
                onShowMoreGroupedChildren={() => showMoreCatalogRows(node.id)}
                onShowFewerGroupedChildren={() => showFewerCatalogRows(node.id)}
                onHighlight={onHighlight}
                onClearNodeHighlights={onClearNodeHighlights}
                onClearAllHighlights={onClearAllHighlights}
                onOpenQualityDetails={openQualityPanel}
              />
            </div>
          );
        })}
      </div>
    </section>
  );
}
