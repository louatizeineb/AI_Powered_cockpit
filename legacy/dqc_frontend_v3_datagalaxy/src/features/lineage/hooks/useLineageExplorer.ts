import { useCallback, useMemo, useState } from "react";
import {
  fetchLineageNeighbors,
  searchLineageEntities,
} from "../api/lineageApi";
import type {
  HighlightDirection,
  HighlightedPath,
  LineageDirection,
  LineageEdge,
  LineageGraphState,
  LineageNode,
  LineagePosition,
  LineageSearchResult,
} from "../types/lineage.types";
import { buildHighlightPath, refreshHighlightPath } from "../utils/lineagePathHighlight";
import { computeStagePositions } from "../utils/lineageStageLayout";

function withDepth(node: LineageSearchResult, depth: number): LineageNode {
  return { ...node, depth };
}

function loadingKey(nodeId: string, direction: LineageDirection) {
  return `${nodeId}:${direction}`;
}

function canonicalRelationshipType(type: string | null | undefined) {
  const compact = String(type || "").replace(/[\s_-]/g, "").toUpperCase();
  if (compact === "ISOUTPUTOF") return "IS_OUTPUT_OF";
  if (compact === "ISINPUTOF") return "IS_INPUT_OF";
  return String(type || "RELATED").replace(/[\s-]+/g, "_").toUpperCase();
}

function normalizeVisualEdge(edge: LineageEdge): LineageEdge {
  const rawType = edge.raw_type || edge.type;
  const type = canonicalRelationshipType(edge.type || rawType);
  const shouldReverseOutput = canonicalRelationshipType(rawType) === "IS_OUTPUT_OF" && !edge.is_visual_reversed;
  const visualSource = edge.visual_source || (shouldReverseOutput ? edge.target : edge.source);
  const visualTarget = edge.visual_target || (shouldReverseOutput ? edge.source : edge.target);
  return {
    ...edge,
    source: visualSource,
    target: visualTarget,
    type,
    raw_type: rawType,
    visual_source: visualSource,
    visual_target: visualTarget,
    is_visual_reversed: Boolean(edge.is_visual_reversed || shouldReverseOutput),
  };
}

export function useLineageExplorer() {
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<LineageSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [loadingExpansions, setLoadingExpansions] = useState<Record<string, boolean>>({});
  const [positions, setPositions] = useState<Record<string, LineagePosition>>({});
  const [graph, setGraph] = useState<LineageGraphState>({
    nodes: [],
    edges: [],
    expanded: { upstream: {}, downstream: {} },
    focusedNodeId: null,
    highlights: [],
  });

  const nodesById = useMemo(
    () => new Map(graph.nodes.map((node) => [node.id, node])),
    [graph.nodes]
  );

  const runSearch = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) return;
    setSearching(true);
    setError("");
    try {
      const response = await searchLineageEntities(trimmed, 20);
      setSearchResults(response.results || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lineage search failed");
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  }, [query]);

  const selectResult = useCallback((result: LineageSearchResult) => {
    const root = withDepth(result, 0);
    setGraph({
      nodes: [root],
      edges: [],
      expanded: { upstream: {}, downstream: {} },
      focusedNodeId: root.id,
      highlights: [],
    });
    setPositions({ [root.id]: { x: 0, y: 0 } });
    setQuery(result.label || result.technical_name || result.node_id);
    setSearchResults([]);
    setError("");
  }, []);

  const focusNode = useCallback((nodeId: string) => {
    setGraph((current) => ({ ...current, focusedNodeId: nodeId }));
  }, []);

  const moveNode = useCallback((nodeId: string, position: LineagePosition) => {
    setPositions((current) => ({
      ...current,
      [nodeId]: position,
    }));
  }, []);

  const resetLayout = useCallback(() => {
    setPositions((current) => computeStagePositions(graph.nodes, graph.edges, current));
  }, [graph.edges, graph.nodes]);

  const applyHighlight = useCallback(
    (nodeId: string, direction: HighlightDirection, color: string) => {
      setGraph((current) => {
        const path = buildHighlightPath(current, nodeId, direction, color);
        const remaining = current.highlights.filter(
          (item) => !(item.sourceNodeId === nodeId && item.direction === direction)
        );
        return {
          ...current,
          highlights: [...remaining, path],
        };
      });
    },
    []
  );

  const clearNodeHighlights = useCallback((nodeId: string) => {
    setGraph((current) => ({
      ...current,
      highlights: current.highlights.filter((item) => item.sourceNodeId !== nodeId),
    }));
  }, []);

  const clearAllHighlights = useCallback(() => {
    setGraph((current) => ({
      ...current,
      highlights: [],
    }));
  }, []);

  const expandNode = useCallback(
    async (nodeId: string, direction: LineageDirection) => {
      const node = nodesById.get(nodeId);
      if (!node || graph.expanded[direction][node.id] || loadingExpansions[loadingKey(node.id, direction)]) {
        return;
      }

      const key = loadingKey(node.id, direction);
      setLoadingExpansions((current) => ({ ...current, [key]: true }));
      setError("");
      try {
        const response = await fetchLineageNeighbors(node.node_id, direction, 50);
        const incoming = response.nodes || [];

        setGraph((current) => {
          const currentNodes = new Map(current.nodes.map((item) => [item.id, item]));
          incoming.forEach((item, index) => {
            const depth = node.depth + (direction === "downstream" ? 1 : -1);
            const existing = currentNodes.get(item.id);
            currentNodes.set(item.id, existing ? { ...existing, ...item } : withDepth(item, depth));
          });

          const currentEdges = new Map(current.edges.map((edge) => [edge.id, edge]));
          (response.edges || []).forEach((edge: LineageEdge) => {
            const normalized = normalizeVisualEdge(edge);
            currentEdges.set(normalized.id, normalized);
          });
          const nextNodes = [...currentNodes.values()];
          const nextEdges = [...currentEdges.values()];
          setPositions((currentPositions) => computeStagePositions(nextNodes, nextEdges, currentPositions));

          return {
            nodes: nextNodes,
            edges: nextEdges,
            expanded: {
              ...current.expanded,
              [direction]: {
                ...current.expanded[direction],
                [node.id]: true,
              },
            },
            focusedNodeId: node.id,
            highlights: current.highlights.map((highlight) => refreshHighlightPath(
              { nodes: nextNodes, edges: nextEdges },
              highlight
            )),
          };
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : `Failed to expand ${direction} lineage`);
      } finally {
        setLoadingExpansions((current) => {
          const next = { ...current };
          delete next[key];
          return next;
        });
      }
    },
    [graph.expanded, loadingExpansions, nodesById]
  );

  return {
    graph,
    positions,
    query,
    searchResults,
    searching,
    error,
    loadingExpansions,
    setQuery,
    runSearch,
    selectResult,
    focusNode,
    moveNode,
    expandNode,
    resetLayout,
    applyHighlight,
    clearNodeHighlights,
    clearAllHighlights,
  };
}
