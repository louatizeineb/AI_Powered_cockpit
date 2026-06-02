import type { HighlightDirection, HighlightedPath, LineageDirection, LineageEdge, LineageNode } from "../types/lineage.types";

type GraphSlice = {
  nodes: LineageNode[];
  edges: LineageEdge[];
};

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
      direction === "downstream" ? edge.source === current : edge.target === current
    );
    edges.forEach((edge) => {
      seenEdges.add(edge.id);
      const nextNodeId = direction === "downstream" ? edge.target : edge.source;
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
  const result =
    direction === "branch"
      ? {
          nodeIds: [
            ...new Set([
              ...walk(graph, sourceNodeId, "upstream").nodeIds,
              ...walk(graph, sourceNodeId, "downstream").nodeIds,
            ]),
          ],
          edgeIds: [
            ...new Set([
              ...walk(graph, sourceNodeId, "upstream").edgeIds,
              ...walk(graph, sourceNodeId, "downstream").edgeIds,
            ]),
          ],
        }
      : walk(graph, sourceNodeId, direction);
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
  const result =
    highlight.direction === "branch"
      ? {
          nodeIds: [
            ...new Set([
              ...walk(graph, highlight.sourceNodeId, "upstream").nodeIds,
              ...walk(graph, highlight.sourceNodeId, "downstream").nodeIds,
            ]),
          ],
          edgeIds: [
            ...new Set([
              ...walk(graph, highlight.sourceNodeId, "upstream").edgeIds,
              ...walk(graph, highlight.sourceNodeId, "downstream").edgeIds,
            ]),
          ],
        }
      : walk(graph, highlight.sourceNodeId, highlight.direction);
  return {
    ...highlight,
    nodeIds: result.nodeIds,
    edgeIds: result.edgeIds,
  };
}
