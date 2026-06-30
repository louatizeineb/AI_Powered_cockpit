import { useCallback, useMemo, useRef, useState } from "react";
import {
  fetchLineageNeighbors,
  fetchLineageSourceContext,
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
import { filterNextLineageStep } from "../utils/lineageNextStepFilter";
import { HORIZONTAL_SPACING, VERTICAL_SPACING } from "../utils/lineageLayout";

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

function isContextRelationship(edge: LineageEdge) {
  const type = canonicalRelationshipType(edge.type || edge.raw_type);
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

function isSourceCatalogNode(node: LineageSearchResult) {
  return ["source", "asset", "structure", "dataset", "field"].includes(String(node.category || "").toLowerCase());
}

function isCatalogContainmentRelationship(edge: LineageEdge) {
  const type = canonicalRelationshipType(edge.type || edge.raw_type);
  return (
    type.includes("HAS_FIELD") ||
    type.includes("HAS_COLUMN") ||
    type.includes("HAS_STRUCTURE") ||
    type.includes("HAS_CONTAINER") ||
    type.includes("CONTAINS")
  );
}

function directExpandedNodeIds(clickedNodeId: string, edges: LineageEdge[], direction: LineageDirection) {
  const ids = new Set<string>();
  edges.forEach((edge) => {
    if (isContextRelationship(edge)) return;
    const source = edge.visual_source || edge.source;
    const target = edge.visual_target || edge.target;
    if (direction === "downstream" && source === clickedNodeId) ids.add(target);
    if (direction === "upstream" && target === clickedNodeId) ids.add(source);
  });
  return ids;
}

export function useLineageExplorer() {
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<LineageSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [loadingExpansions, setLoadingExpansions] = useState<Record<string, boolean>>({});
  const [loadingSourceContexts, setLoadingSourceContexts] = useState<Record<string, boolean>>({});
  const [positions, setPositions] = useState<Record<string, LineagePosition>>({});
  const searchAbortController = useRef<AbortController | null>(null);
  const [graph, setGraph] = useState<LineageGraphState>({
    nodes: [],
    edges: [],
    expanded: { upstream: {}, downstream: {} },
    sourceContextExpanded: {},
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
    searchAbortController.current?.abort();
    const controller = new AbortController();
    searchAbortController.current = controller;
    setSearching(true);
    setError("");
    try {
      const response = await searchLineageEntities(trimmed, 20, controller.signal);
      if (controller.signal.aborted) return;
      setSearchResults(response.results || []);
    } catch (err) {
      if (controller.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Lineage search failed");
      setSearchResults([]);
    } finally {
      if (searchAbortController.current === controller) {
        setSearching(false);
      }
    }
  }, [query]);

  const selectResult = useCallback((result: LineageSearchResult) => {
    const root = withDepth(result, 0);
    setGraph({
      nodes: [root],
      edges: [],
      expanded: { upstream: {}, downstream: {} },
      sourceContextExpanded: {},
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

        setGraph((current) => {
          // Strict demand-driven lineage rule:
          // the backend may return extra one-hop objects around the same DPI/DP,
          // for example the produced output field ID2 while expanding input ID1.
          // Those objects are true lineage, but they are NOT the next demanded
          // step. They must stay hidden until the user clicks + on the DPI/DP.
          const depth = node.depth + (direction === "downstream" ? 1 : -1);
          const responseNodes = (response.nodes || []).map((item) => withDepth(item, depth));
          const normalizedResponseEdges = (response.edges || []).map((edge: LineageEdge) => normalizeVisualEdge(edge));
          const filtered = filterNextLineageStep(
            node,
            responseNodes,
            normalizedResponseEdges,
            direction
          );
          const directlyDemanded = directExpandedNodeIds(node.id, filtered.edges, direction);

          const allowedNodeIds = new Set(filtered.nodes.map((item) => item.id));

          const currentNodes = new Map(current.nodes.map((item) => [item.id, item]));
          filtered.nodes.forEach((item) => {
            const existing = currentNodes.get(item.id);
            currentNodes.set(item.id, existing ? { ...existing, ...item } : withDepth(item, depth));
          });

          const currentEdges = new Map(current.edges.map((edge) => [edge.id, edge]));
          filtered.edges.forEach((edge: LineageEdge) => {
            const normalized = normalizeVisualEdge(edge);
            const source = normalized.visual_source || normalized.source;
            const target = normalized.visual_target || normalized.target;

            // Only merge edges that connect the clicked node, already visible nodes,
            // or the strictly allowed next/context nodes. Never merge hidden future
            // lineage edges, because that is what makes future IDs appear early.
            const sourceAllowed = source === node.id || currentNodes.has(source) || allowedNodeIds.has(source);
            const targetAllowed = target === node.id || currentNodes.has(target) || allowedNodeIds.has(target);
            if (sourceAllowed && targetAllowed) {
              currentEdges.set(normalized.id, normalized);
            }
          });

          const nextNodes = [...currentNodes.values()];
          const visibleNodeIds = new Set(nextNodes.map((item) => item.id));
          const nextEdges = [...currentEdges.values()].filter((edge) => {
            const source = edge.visual_source || edge.source;
            const target = edge.visual_target || edge.target;
            return visibleNodeIds.has(source) && visibleNodeIds.has(target);
          });

          setPositions((currentPositions) => {
            const seededPositions = { ...currentPositions };
            const clickedPosition = seededPositions[node.id];
            if (clickedPosition && directlyDemanded.size) {
              const ordered = [...directlyDemanded];
              ordered.forEach((id, index) => {
                const centered = index - (ordered.length - 1) / 2;
                seededPositions[id] = {
                  x: clickedPosition.x + (direction === "downstream" ? HORIZONTAL_SPACING : -HORIZONTAL_SPACING),
                  y: clickedPosition.y + centered * VERTICAL_SPACING,
                };
              });
            }
            return computeStagePositions(nextNodes, nextEdges, seededPositions);
          });

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
            sourceContextExpanded: current.sourceContextExpanded,
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

  const expandSourceContext = useCallback(
    async (nodeId: string) => {
      const node = nodesById.get(nodeId);
      const catalogOffset = Number(node?.properties?.source_context_next_offset || 0);
      const hasMore = node?.properties?.source_context_has_more === true;
      if (!node || loadingSourceContexts[node.id] || (graph.sourceContextExpanded[node.id] && !hasMore)) return;

      setLoadingSourceContexts((current) => ({ ...current, [node.id]: true }));
      setError("");
      try {
        const response = await fetchLineageSourceContext(node.node_id, catalogOffset);
        setGraph((current) => {
          const currentNodes = new Map(current.nodes.map((item) => [item.id, item]));
          const existingCenter = currentNodes.get(response.center.id);
          if (existingCenter) {
            currentNodes.set(response.center.id, { ...existingCenter, ...response.center, depth: existingCenter.depth });
          }
          const catalogNodes = (response.nodes || []).filter(isSourceCatalogNode);
          catalogNodes.forEach((item) => {
            const existing = currentNodes.get(item.id);
            currentNodes.set(item.id, existing ? { ...existing, ...item } : withDepth(item, node.depth));
          });

          const catalogNodeIds = new Set([response.center.id, ...catalogNodes.map((item) => item.id)]);
          const currentEdges = new Map(current.edges.map((edge) => [edge.id, edge]));
          (response.edges || []).filter((edge) => {
            const source = edge.visual_source || edge.source;
            const target = edge.visual_target || edge.target;
            return isCatalogContainmentRelationship(edge) && catalogNodeIds.has(source) && catalogNodeIds.has(target);
          }).forEach((edge) => {
            const normalized = normalizeVisualEdge(edge);
            currentEdges.set(normalized.id, normalized);
          });

          const nextNodes = [...currentNodes.values()];
          const visibleNodeIds = new Set(nextNodes.map((item) => item.id));
          const nextEdges = [...currentEdges.values()].filter((edge) => {
            const source = edge.visual_source || edge.source;
            const target = edge.visual_target || edge.target;
            return visibleNodeIds.has(source) && visibleNodeIds.has(target);
          });
          setPositions((currentPositions) => computeStagePositions(nextNodes, nextEdges, currentPositions));

          return {
            ...current,
            nodes: nextNodes,
            edges: nextEdges,
            sourceContextExpanded: {
              ...current.sourceContextExpanded,
              [node.id]: true,
            },
          };
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load source catalog");
      } finally {
        setLoadingSourceContexts((current) => {
          const next = { ...current };
          delete next[node.id];
          return next;
        });
      }
    },
    [graph.sourceContextExpanded, loadingSourceContexts, nodesById]
  );

  return {
    graph,
    positions,
    query,
    searchResults,
    searching,
    error,
    loadingExpansions,
    loadingSourceContexts,
    setQuery,
    runSearch,
    selectResult,
    focusNode,
    moveNode,
    expandNode,
    expandSourceContext,
    resetLayout,
    applyHighlight,
    clearNodeHighlights,
    clearAllHighlights,
  };
}
