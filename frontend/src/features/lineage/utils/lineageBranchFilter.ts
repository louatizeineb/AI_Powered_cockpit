import type { LineageEdge, LineageNode } from "../types/lineage.types";
import { classifyLineageStage } from "./lineageStageClassifier";

type GroupingLike = {
  parentByChildId?: Record<string, string>;
};

function edgeSource(edge: LineageEdge) {
  return edge.visual_source || edge.source;
}

function edgeTarget(edge: LineageEdge) {
  return edge.visual_target || edge.target;
}

function visibleId(id: string, grouping?: GroupingLike) {
  return grouping?.parentByChildId?.[id] || id;
}

function visualSource(edge: LineageEdge, grouping?: GroupingLike) {
  return visibleId(edgeSource(edge), grouping);
}

function visualTarget(edge: LineageEdge, grouping?: GroupingLike) {
  return visibleId(edgeTarget(edge), grouping);
}

function nodeRole(node?: LineageNode) {
  return classifyLineageStage(node || {});
}

function isProcessingRole(role: string) {
  return role === "data_processing" || role === "data_processing_item";
}

function isAssetRole(role: string) {
  return !isProcessingRole(role) && role !== "usage" && role !== "control";
}

function visualNodeRoles(nodes: LineageNode[], grouping?: GroupingLike) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const roles = new Map<string, string>();

  nodes.forEach((node) => {
    const id = visibleId(node.id, grouping);
    const visibleNode = byId.get(id) || node;
    const role = nodeRole(visibleNode);
    const previous = roles.get(id);
    if (!previous || isProcessingRole(role)) {
      roles.set(id, role);
    }
  });

  return roles;
}

function key(source: string, target: string) {
  return `${source}->${target}`;
}

function hasProcessingBridge(
  source: string,
  target: string,
  edges: LineageEdge[],
  roles: Map<string, string>,
  grouping?: GroupingLike
) {
  const processingTargets = new Set<string>();

  edges.forEach((edge) => {
    const left = visualSource(edge, grouping);
    const right = visualTarget(edge, grouping);
    if (left !== source) return;
    if (isProcessingRole(roles.get(right) || "")) processingTargets.add(right);
  });

  if (!processingTargets.size) return false;

  return edges.some((edge) => {
    const left = visualSource(edge, grouping);
    const right = visualTarget(edge, grouping);
    return processingTargets.has(left) && right === target;
  });
}

export function filterDpiStoryEdges(
  nodes: LineageNode[],
  edges: LineageEdge[],
  grouping?: GroupingLike
) {
  const roles = visualNodeRoles(nodes, grouping);
  const uniquePairs = new Set<string>();

  return edges.filter((edge) => {
    const source = visualSource(edge, grouping);
    const target = visualTarget(edge, grouping);
    if (!source || !target || source === target) return false;

    const pair = key(source, target);
    if (uniquePairs.has(pair)) return false;
    uniquePairs.add(pair);

    const sourceRole = roles.get(source) || "";
    const targetRole = roles.get(target) || "";
    const directAssetToAsset = isAssetRole(sourceRole) && isAssetRole(targetRole);

    if (directAssetToAsset && hasProcessingBridge(source, target, edges, roles, grouping)) {
      return false;
    }

    return true;
  });
}
