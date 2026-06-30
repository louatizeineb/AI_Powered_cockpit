import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
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
  boardBounds,
  CARD_HEIGHT,
  CARD_WIDTH,
  toBoardPosition,
} from "../utils/lineageLayout";
import { buildGroupingFromGraph } from "../utils/lineageGrouping";

type LineageCanvasProps = {
  graph: LineageGraphState;
  positions: Record<string, LineagePosition>;
  loading: Record<string, boolean>;
  onFocus: (nodeId: string) => void;
  onMoveNode: (nodeId: string, position: LineagePosition) => void;
  onExpand: (nodeId: string, direction: LineageDirection) => void;
  onHighlight: (nodeId: string, direction: HighlightDirection, color: string) => void;
  onClearNodeHighlights: (nodeId: string) => void;
  onClearAllHighlights: () => void;
  onResetLayout: () => void;
};

const CANVAS_RUNWAY_X = 700;
const CANVAS_RUNWAY_Y = 900;

function edgePath(edge: LineageEdge, positions: Record<string, LineagePosition>) {
  const source = positions[edge.source];
  const target = positions[edge.target];
  if (!source || !target) return "";
  const leftToRight = source.x <= target.x;
  const startX = source.x + (leftToRight ? CARD_WIDTH : 0);
  const startY = source.y + CARD_HEIGHT / 2;
  const endX = target.x + (leftToRight ? 0 : CARD_WIDTH);
  const endY = target.y + CARD_HEIGHT / 2;
  const curve = Math.max(80, Math.abs(endX - startX) * 0.48);
  const c1x = startX + (leftToRight ? curve : -curve);
  const c2x = endX - (leftToRight ? curve : -curve);
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
  return `${source}->${target}:${edge.type}`;
}

export default function LineageCanvas({
  graph,
  positions,
  loading,
  onFocus,
  onMoveNode,
  onExpand,
  onHighlight,
  onClearNodeHighlights,
  onClearAllHighlights,
  onResetLayout,
}: LineageCanvasProps) {
  const [zoom, setZoom] = useState(0.9);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isPanning, setIsPanning] = useState(false);
  const [draggingNodeId, setDraggingNodeId] = useState<string | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
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
  const bounds = useMemo(() => boardBounds(positions), [positions]);
  const boardSize = useMemo(
    () => ({
      width: bounds.width + CANVAS_RUNWAY_X,
      height: bounds.height + CANVAS_RUNWAY_Y,
    }),
    [bounds.height, bounds.width]
  );
  const boardPositions = useMemo(() => {
    const next: Record<string, LineagePosition> = {};
    Object.entries(positions).forEach(([id, position]) => {
      next[id] = toBoardPosition(position, bounds);
    });
    return next;
  }, [bounds, positions]);
  const highlightByNodeId = useMemo(() => {
    const mapping: Record<string, string | null> = {};
    graph.highlights.forEach((highlight) => {
      highlight.nodeIds.forEach((nodeId) => {
        mapping[nodeId] = highlight.color;
      });
    });
    return mapping;
  }, [graph.highlights]);
  const grouping = useMemo(
    () => buildGroupingFromGraph(graph.nodes, graph.edges, highlightByNodeId),
    [graph.edges, graph.nodes, highlightByNodeId]
  );
  const visualEdges = useMemo(() => {
    const mapped = new Map<string, LineageEdge>();
    graph.edges.forEach((edge) => {
      const visualSource = edge.visual_source || edge.source;
      const visualTarget = edge.visual_target || edge.target;
      const source = grouping.parentByChildId[visualSource] || visualSource;
      const target = grouping.parentByChildId[visualTarget] || visualTarget;
      if (source === target) return;
      const key = visualEdgeKey(edge, source, target);
      const existing = mapped.get(key);
      if (!existing || (!edgeHighlightColor(existing.id, graph.highlights) && edgeHighlightColor(edge.id, graph.highlights))) {
        mapped.set(key, { ...edge, source, target });
      }
    });
    return [...mapped.values()];
  }, [graph.edges, graph.highlights, grouping.parentByChildId]);

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
    const fitX = (shell.clientWidth - 120) / Math.max(bounds.width, 1);
    const fitY = (shell.clientHeight - 120) / Math.max(bounds.height, 1);
    const nextZoom = Math.max(0.42, Math.min(maxZoom, fitX, fitY));
    setZoom(nextZoom);
    requestAnimationFrame(() => {
      centerBoard(nextZoom);
    });
  }

  useEffect(() => {
    if (graph.nodes.length === 1) {
      requestAnimationFrame(() => fitToGraph(0.92));
      lastNodeCountRef.current = 1;
      return;
    }
    if (graph.nodes.length > lastNodeCountRef.current && lastNodeCountRef.current > 0) {
      setZoom((value) => Math.max(0.5, value - 0.06));
      requestAnimationFrame(() => centerBoard(Math.max(0.5, zoom - 0.06)));
    }
    lastNodeCountRef.current = graph.nodes.length;
  }, [graph.nodes.length, bounds.width, bounds.height]);

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

  function startNodeDrag(event: ReactMouseEvent<HTMLDivElement>, nodeId: string) {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest("button")) return;
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
      onMouseDown={startPan}
      onContextMenu={(event) => event.preventDefault()}
    >
      <div className="plex-canvas-controls">
        <button type="button" onClick={() => fitToGraph(1.05)} title="Fit to graph">Fit</button>
        <button type="button" onClick={() => centerBoard()} title="Center canvas">Center</button>
        <button type="button" onClick={onResetLayout} title="Reset layout">Reset</button>
        <button type="button" onClick={onClearAllHighlights} title="Clear highlights">Clear</button>
        <FullscreenToggle active={isFullscreen} onToggle={toggleFullscreen} />
        <button type="button" onClick={() => setZoom((value) => Math.max(0.45, value - 0.1))} title="Zoom out">-</button>
        <strong>{Math.round(zoom * 100)}%</strong>
        <button type="button" onClick={() => setZoom((value) => Math.min(1.4, value + 0.1))} title="Zoom in">+</button>
      </div>
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
          </defs>
          {visualEdges.map((edge) => (
            <path
              key={edge.id}
              className={`plex-edge ${edgeHighlightColor(edge.id, graph.highlights) ? "highlighted" : ""}`}
              style={{ ["--plex-edge-highlight" as string]: edgeHighlightColor(edge.id, graph.highlights) || "#7da2ff" } as CSSProperties}
              d={edgePath(edge, boardPositions)}
            >
              <title>{edge.type}</title>
            </path>
          ))}
        </svg>

        {graph.nodes.map((node) => {
          if (grouping.hiddenNodeIds[node.id]) return null;
          const position = boardPositions[node.id];
          if (!position) return null;
          return (
            <div
              key={node.id}
              className={`plex-node-position ${draggingNodeId === node.id ? "dragging" : ""}`}
              style={{ left: position.x, top: position.y, width: CARD_WIDTH }}
              onMouseDown={(event) => startNodeDrag(event, node.id)}
            >
              <LineageNodeCard
                node={node}
                focused={graph.focusedNodeId === node.id}
                highlightColor={cardHighlightColor(node.id)}
                hasHighlight={graph.highlights.some((item) => item.sourceNodeId === node.id)}
                groupedChildren={grouping.groupedByParentId[node.id] || []}
                expanded={graph.expanded}
                loading={loading}
                onFocus={onFocus}
                onExpand={onExpand}
                onHighlight={onHighlight}
                onClearNodeHighlights={onClearNodeHighlights}
                onClearAllHighlights={onClearAllHighlights}
              />
            </div>
          );
        })}
      </div>
    </section>
  );
}
