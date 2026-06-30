import type { LineageDirection, LineageEdge, LineageNode } from "../types/lineage.types";
import { canonicalRelType, classifyLineageStage } from "./lineageStageClassifier";

function edgeSource(edge: LineageEdge) {
  return edge.visual_source || edge.source;
}

function edgeTarget(edge: LineageEdge) {
  return edge.visual_target || edge.target;
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

function isProcessingRole(role: string) {
  return role === "data_processing" || role === "data_processing_item";
}

function isProcessingItemRole(role: string) {
  return role === "data_processing_item";
}

function isProcessingParentRole(role: string) {
  return role === "data_processing";
}

function isUsageRole(role: string) {
  return role === "usage";
}

function isControlRole(role: string) {
  return role === "control";
}

function isAssetRole(role: string) {
  return !isProcessingRole(role) && !isUsageRole(role) && !isControlRole(role);
}

function isAssetContextRole(role: string) {
  return role === "golden_source" || role === "source_asset" || role === "intermediate_asset" || role === "final_asset";
}

function roleOf(node: LineageNode) {
  return classifyLineageStage(node);
}

function preferredNextRoles(clickedRole: string, candidates: LineageNode[]) {
  const roles = candidates.map(roleOf);
  const hasProcessing = roles.some(isProcessingRole);
  const hasAsset = roles.some(isAssetRole);
  const hasUsage = roles.some(isUsageRole);

  // The lineage story alternates by stage. Do not show a produced asset before
  // the processing step that consumes the clicked asset, and do not show the
  // next processing step before the produced intermediate asset is requested.
  // When both a DP and a DPI are linked to the clicked field, the next demanded
  // lineage item is the DPI. The DP is only kept as a card container/context.
  // A source is the exception: its direct usage children are terminal outcomes,
  // so keep them visible beside any next DPI instead of hiding them behind a
  // processing-first choice.
  if (clickedRole === "source_asset" && hasUsage && roles.some(isProcessingItemRole)) {
    return (role: string) => isProcessingItemRole(role) || isUsageRole(role);
  }
  if (clickedRole === "source_asset" && hasUsage && hasProcessing) {
    return (role: string) => isProcessingRole(role) || isUsageRole(role);
  }
  if (isAssetRole(clickedRole) && roles.some(isProcessingItemRole)) {
    return (role: string) => isProcessingItemRole(role);
  }
  if (isAssetRole(clickedRole) && hasProcessing) return (role: string) => isProcessingRole(role);
  if (isProcessingRole(clickedRole) && hasAsset) return (role: string) => isAssetRole(role);
  if (isAssetRole(clickedRole) && hasUsage) return (role: string) => isUsageRole(role);

  return (_role: string) => true;
}

function canKeepContextNode(anchor: LineageNode, context: LineageNode) {
  const anchorRole = roleOf(anchor);
  const contextRole = roleOf(context);

  // A DPI is rendered inside its parent DP card. This is the only processing
  // context we auto-add. We do not auto-add sibling DPIs or produced assets.
  if (isProcessingItemRole(anchorRole) && isProcessingParentRole(contextRole)) {
    return true;
  }

  // A field/asset can be rendered inside its parent table/structure/source card.
  // This is one-hop card context only, not another lineage stage.
  if (isAssetRole(anchorRole) && isAssetContextRole(contextRole)) {
    return true;
  }

  // A concrete usage is rendered inside its direct usage folder.
  if (isUsageRole(anchorRole) && isUsageRole(contextRole)) {
    return true;
  }

  return false;
}

function contextEdgesForKeptDirectNodes(
  directIds: Set<string>,
  incomingNodesById: Map<string, LineageNode>,
  incomingEdges: LineageEdge[]
) {
  const keptNodeIds = new Set<string>();
  const keptEdgeIds = new Set<string>();

  incomingEdges.forEach((edge) => {
    if (!isContextEdge(edge)) return;

    const source = edgeSource(edge);
    const target = edgeTarget(edge);
    const sourceIsDirect = directIds.has(source);
    const targetIsDirect = directIds.has(target);

    // Strict one-hop context only. Do not run a graph closure here: closures are
    // exactly what make “not yet demanded” lineage stages appear too early.
    if (sourceIsDirect === targetIsDirect) return;

    const directId = sourceIsDirect ? source : target;
    const contextId = sourceIsDirect ? target : source;
    const directNode = incomingNodesById.get(directId);
    const contextNode = incomingNodesById.get(contextId);
    if (!directNode || !contextNode) return;

    if (!canKeepContextNode(directNode, contextNode)) return;

    keptNodeIds.add(contextId);
    keptEdgeIds.add(edge.id);
  });

  return { keptNodeIds, keptEdgeIds };
}

function upstreamOwnerContext(
  clicked: LineageNode,
  incomingNodes: LineageNode[],
  incomingEdges: LineageEdge[]
) {
  const byId = new Map(incomingNodes.map((node) => [node.id, node]));
  const keptNodeIds = new Set<string>();
  const keptEdgeIds = new Set<string>();

  incomingEdges.forEach((edge) => {
    if (!isContextEdge(edge)) return;
    const source = edgeSource(edge);
    const target = edgeTarget(edge);
    if (source !== clicked.id && target !== clicked.id) return;
    const type = canonicalRelType(edge.type || edge.raw_type);
    const catalogOwnerEdge = (
      type.includes("HAS_FIELD") ||
      type.includes("HAS_COLUMN") ||
      type.includes("HAS_STRUCTURE") ||
      type.includes("HAS_CONTAINER") ||
      type.includes("CONTAINS")
    );
    if (catalogOwnerEdge && target !== clicked.id) return;

    const contextId = source === clicked.id ? target : source;
    const contextNode = byId.get(contextId);
    if (!contextNode || !canKeepContextNode(clicked, contextNode)) return;

    keptNodeIds.add(contextId);
    keptEdgeIds.add(edge.id);
  });

  return {
    nodes: incomingNodes.filter((node) => keptNodeIds.has(node.id)),
    edges: incomingEdges.filter((edge) => keptEdgeIds.has(edge.id)),
  };
}

export function filterNextLineageStep(
  clicked: LineageNode,
  incomingNodes: LineageNode[],
  incomingEdges: LineageEdge[],
  direction: LineageDirection
) {
  const byId = new Map(incomingNodes.map((node) => [node.id, node]));

  // Only the lineage edges that touch the clicked node are allowed to decide
  // the next visible step. Everything else from the response is treated as
  // future lineage and is hidden until its own + button is clicked.
  const directLineageEdges = incomingEdges.filter((edge) => {
    if (isContextEdge(edge)) return false;
    return direction === "downstream"
      ? edgeSource(edge) === clicked.id
      : edgeTarget(edge) === clicked.id;
  });

  const directNodeIds = new Set(
    directLineageEdges.map((edge) => (direction === "downstream" ? edgeTarget(edge) : edgeSource(edge)))
  );

  const directNodes = [...directNodeIds]
    .map((id) => byId.get(id))
    .filter((node): node is LineageNode => Boolean(node));

  if (!directNodes.length) {
    // Searching a field starts with its compact virtual card. An upstream click
    // may only return HAS_FIELD/PART_OF context, which is still useful: reveal
    // the owning structure/DP card and render the selected row inside it.
    if (direction === "upstream") {
      return upstreamOwnerContext(clicked, incomingNodes, incomingEdges);
    }
    return { nodes: [], edges: [] };
  }

  const keepRole = preferredNextRoles(roleOf(clicked), directNodes);
  const keptDirectIds = new Set(directNodes.filter((node) => keepRole(roleOf(node))).map((node) => node.id));

  // Fallback: if role classification is uncertain, keep the direct one-hop
  // neighbors, but still do not keep any unrelated/future response nodes.
  if (!keptDirectIds.size) {
    directNodes.forEach((node) => keptDirectIds.add(node.id));
  }

  const keptNodeIds = new Set<string>(keptDirectIds);
  const keptEdgeIds = new Set<string>();

  directLineageEdges.forEach((edge) => {
    const nextId = direction === "downstream" ? edgeTarget(edge) : edgeSource(edge);
    if (keptDirectIds.has(nextId)) keptEdgeIds.add(edge.id);
  });

  // If the backend returns both ID1 -> DP and ID1 -> DPI, the DP should exist
  // only so the DPI can be rendered inside its parent DP card. We keep the DP
  // node as grouping context, but we do not keep the direct ID1 -> DP edge;
  // the visible lineage step remains ID1 -> DPI.
  const keptHasDpi = [...keptDirectIds].some((id) => {
    const node = byId.get(id);
    return node ? isProcessingItemRole(roleOf(node)) : false;
  });
  if (keptHasDpi) {
    directNodes.forEach((candidate) => {
      const candidateRole = roleOf(candidate);
      if (isProcessingParentRole(candidateRole) && !keptDirectIds.has(candidate.id)) {
        keptNodeIds.add(candidate.id);
      }
    });
  }

  const context = contextEdgesForKeptDirectNodes(keptDirectIds, byId, incomingEdges);
  context.keptNodeIds.forEach((id) => keptNodeIds.add(id));
  context.keptEdgeIds.forEach((id) => keptEdgeIds.add(id));

  return {
    nodes: incomingNodes.filter((node) => keptNodeIds.has(node.id)),
    edges: incomingEdges.filter((edge) => keptEdgeIds.has(edge.id)),
  };
}
