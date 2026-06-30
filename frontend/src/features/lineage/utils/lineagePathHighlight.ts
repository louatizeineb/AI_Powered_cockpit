import type { HighlightDirection, HighlightedPath, LineageDirection, LineageEdge, LineageNode } from "../types/lineage.types";
import { filterDpiStoryEdges } from "./lineageBranchFilter";

type GraphSlice = {
  nodes: LineageNode[];
  edges: LineageEdge[];
};

function edgeSource(edge: LineageEdge) {
  return edge.visual_source || edge.source;
}

function edgeTarget(edge: LineageEdge) {
  return edge.visual_target || edge.target;
}

function walk(
  graph: GraphSlice,
  sourceNodeId: string,
  direction: LineageDirection
): { nodeIds: string[]; edgeIds: string[] } {
  const seenNodes = new Set<string>([sourceNodeId]);
  const seenEdges = new Set<string>();
  const queue = [sourceNodeId];

  while (queue.length) {
    const current = queue.shift()!;
    const edges = graph.edges.filter((edge) =>
      direction === "downstream" ? edgeSource(edge) === current : edgeTarget(edge) === current
    );
    edges.forEach((edge) => {
      seenEdges.add(edge.id);
      const nextNodeId = direction === "downstream" ? edgeTarget(edge) : edgeSource(edge);
      if (!seenNodes.has(nextNodeId)) {
        seenNodes.add(nextNodeId);
        queue.push(nextNodeId);
      }
    });
  }

  return { nodeIds: [...seenNodes], edgeIds: [...seenEdges] };
}

export function buildHighlightPath(
  graph: GraphSlice,
  sourceNodeId: string,
  direction: HighlightDirection,
  color: string
): HighlightedPath {
  const storyGraph = {
    nodes: graph.nodes,
    edges: filterDpiStoryEdges(graph.nodes, graph.edges),
  };
  const result =
    direction === "branch"
      ? {
          nodeIds: [...new Set([...walk(storyGraph, sourceNodeId, "upstream").nodeIds, ...walk(storyGraph, sourceNodeId, "downstream").nodeIds])],
          edgeIds: [...new Set([...walk(storyGraph, sourceNodeId, "upstream").edgeIds, ...walk(storyGraph, sourceNodeId, "downstream").edgeIds])],
        }
      : walk(storyGraph, sourceNodeId, direction);
  return {
    id: `${sourceNodeId}:${direction}`,
    sourceNodeId,
    direction,
    color,
    nodeIds: result.nodeIds,
    edgeIds: result.edgeIds,
  };
}

export function refreshHighlightPath(graph: GraphSlice, highlight: HighlightedPath): HighlightedPath {
  const refreshed = buildHighlightPath(graph, highlight.sourceNodeId, highlight.direction, highlight.color);
  return { ...highlight, nodeIds: refreshed.nodeIds, edgeIds: refreshed.edgeIds };
}
