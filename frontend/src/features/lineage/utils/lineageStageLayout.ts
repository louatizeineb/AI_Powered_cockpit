import type { LineageDirection, LineageEdge, LineageNode, LineagePosition } from "../types/lineage.types";
import { CARD_HEIGHT, HORIZONTAL_SPACING, VERTICAL_SPACING } from "./lineageLayout";
import { canonicalRelType, isProcessingItemLike, isProcessingLike, isUsageLike, roleSortWeight } from "./lineageStageClassifier";

function splitPath(path: string | null | undefined) {
  return String(path || "").split(/[\\/>|]+/g).map((part) => part.trim()).filter(Boolean);
}

function parentPathKey(node: LineageNode) {
  const parts = splitPath(node.path || node.path_full || String(node.properties?.path || ""));
  return parts.length > 1 ? parts.slice(0, -1).join("/").toLowerCase() : "";
}

function nodePathKey(node: LineageNode) {
  return splitPath(node.path || node.path_full || String(node.properties?.path || "")).join("/").toLowerCase();
}

function buildVisualParentMap(nodes: LineageNode[], edges: LineageEdge[]) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const parentByChild = new Map<string, string>();

  function text(value: unknown) {
    return String(value || "").trim();
  }

  function isField(node: LineageNode) {
    const category = text(node.category).toLowerCase();
    const type = text(node.type).toLowerCase();
    return category === "field" || type.includes("field") || type.includes("column");
  }

  function isFieldParent(node: LineageNode) {
    const category = text(node.category).toLowerCase();
    const type = text(node.type).toLowerCase();
    return ["source", "structure", "dataset"].includes(category) || /source|table|structure|dataset/.test(type);
  }

  function isCatalogParent(node: LineageNode) {
    const category = text(node.category).toLowerCase();
    const type = text(node.type).toLowerCase();
    return ["source", "structure", "dataset", "asset", "usage"].includes(category) || /source|table|structure|dataset|container|directory|usage/.test(type);
  }

  function isSource(node: LineageNode) {
    return text(node.category).toLowerCase() === "source" || text(node.type).toLowerCase().includes("source");
  }

  const catalogParentByChild = new Map<string, string>();

  edges.forEach((edge) => {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    const type = canonicalRelType(edge.type || edge.raw_type);
    if (!source || !target) return;
    if (type.includes("PART_OF") || type.includes("PROCESSING_CONTEXT")) {
      if (isProcessingLike(source) && isProcessingItemLike(target)) parentByChild.set(target.id, source.id);
      if (isProcessingLike(target) && isProcessingItemLike(source)) parentByChild.set(source.id, target.id);
    }
    if (type.includes("HAS_FIELD") || type.includes("HAS_COLUMN") || type.includes("HAS_STRUCTURE") || type.includes("HAS_CONTAINER") || type.includes("CONTAINS")) {
      if (isFieldParent(source) && isField(target)) parentByChild.set(target.id, source.id);
      if (isFieldParent(target) && isField(source)) parentByChild.set(source.id, target.id);
      if (isCatalogParent(source) && (isCatalogParent(target) || isField(target))) catalogParentByChild.set(target.id, source.id);
      if (isCatalogParent(target) && (isCatalogParent(source) || isField(source))) catalogParentByChild.set(source.id, target.id);
    }
  });

  nodes.forEach((node) => {
    if (isSource(node)) return;
    const seen = new Set<string>();
    let parentId = catalogParentByChild.get(node.id);
    while (parentId && !seen.has(parentId)) {
      seen.add(parentId);
      const parent = byId.get(parentId);
      if (!parent) break;
      if (isSource(parent)) {
        parentByChild.set(node.id, parent.id);
        break;
      }
      parentId = catalogParentByChild.get(parent.id);
    }
  });

  const processingNodes = nodes.filter((node) => isProcessingLike(node));
  nodes.filter((node) => isProcessingItemLike(node)).forEach((item) => {
    if (parentByChild.has(item.id)) return;
    const key = parentPathKey(item);
    const parent = processingNodes.find((candidate) => item.parent_node_id === candidate.node_id || key === nodePathKey(candidate));
    if (parent) parentByChild.set(item.id, parent.id);
  });

  nodes.filter(isField).forEach((field) => {
    if (parentByChild.has(field.id)) return;
    const parent = nodes.find((candidate) => {
      if (!isFieldParent(candidate)) return false;
      return Boolean(
        field.parent_node_id && field.parent_node_id === candidate.node_id ||
        field.group_id && field.group_id === candidate.node_id ||
        field.group_id && field.group_id === candidate.id ||
        field.parent_label && field.parent_label === candidate.label
      );
    });
    if (parent) parentByChild.set(field.id, parent.id);
  });

  return parentByChild;
}

function resolveNodeId(id: string, parentByChild: Map<string, string>) {
  return parentByChild.get(id) || id;
}

function visibleLineageEdges(nodes: LineageNode[], edges: LineageEdge[], parentByChild: Map<string, string>) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const result: Array<{ source: string; target: string; type: string }> = [];

  edges.forEach((edge) => {
    const type = canonicalRelType(edge.type || edge.raw_type);
    if (type.includes("PART_OF") || type.includes("PROCESSING_CONTEXT") || type.includes("HAS_FIELD") || type.includes("HAS_STRUCTURE") || type.includes("HAS_CONTAINER") || type.includes("CONTAINS")) return;
    const source = resolveNodeId(edge.visual_source || edge.source, parentByChild);
    const target = resolveNodeId(edge.visual_target || edge.target, parentByChild);
    if (!source || !target || source === target || !byId.has(source) || !byId.has(target)) return;
    result.push({ source, target, type });
  });

  return result;
}

export function stageAwareExpansionPositions(
  clicked: LineageNode,
  incoming: LineageNode[],
  direction: LineageDirection,
  clickedPosition: LineagePosition
): LineagePosition[] {
  const nextX = clickedPosition.x + (direction === "downstream" ? HORIZONTAL_SPACING : -HORIZONTAL_SPACING);
  const sorted = [...incoming].sort((a, b) => roleSortWeight(a) - roleSortWeight(b) || String(a.label).localeCompare(String(b.label)));
  return sorted.map((node, index) => {
    const centered = index - (sorted.length - 1) / 2;
    return {
      x: nextX,
      y: clickedPosition.y + centered * VERTICAL_SPACING,
    };
  });
}

export function computeStagePositions(
  nodes: LineageNode[],
  edges: LineageEdge[],
  previous: Record<string, LineagePosition> = {}
): Record<string, LineagePosition> {
  if (!nodes.length) return {};

  const parentByChild = buildVisualParentMap(nodes, edges);
  const lineageEdges = visibleLineageEdges(nodes, edges, parentByChild);
  const visibleNodes = nodes.filter((node) => !parentByChild.has(node.id));
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  const stages = new Map<string, number>();

  visibleNodes.forEach((node) => {
    const previousX = previous[node.id]?.x;
    stages.set(node.id, previousX === undefined ? 0 : Math.round(previousX / HORIZONTAL_SPACING));
  });

  // If the demanded lineage row is grouped inside a parent card, the visible parent
  // must inherit the row's lineage stage. This is what places payload/class_name
  // to the right of the DP that produced class_name, instead of in a catalog column.
  nodes.forEach((node) => {
    const parent = parentByChild.get(node.id);
    const childPosition = previous[node.id];
    if (!parent || !childPosition || !visibleIds.has(parent)) return;
    stages.set(parent, Math.round(childPosition.x / HORIZONTAL_SPACING));
  });

  // Newly expanded nodes should appear on the correct side of the node that fetched them,
  // then the semantic edge pass below can stretch the story further right if needed.
  lineageEdges.forEach((edge) => {
    const sourceKnown = previous[edge.source] !== undefined;
    const targetKnown = previous[edge.target] !== undefined;
    if (sourceKnown && !targetKnown) {
      const sourceStage = stages.get(edge.source) ?? 0;
      stages.set(edge.target, sourceStage + 1);
    }
    if (targetKnown && !sourceKnown) {
      const targetStage = stages.get(edge.target) ?? 0;
      stages.set(edge.source, targetStage - 1);
    }
  });

  if (![...stages.values()].some((stage) => stage !== 0)) {
    const roots = visibleNodes
      .filter((node) => !lineageEdges.some((edge) => edge.target === node.id))
      .map((node) => node.id);
    roots.forEach((id) => stages.set(id, 0));
  }

  // Enforce semantic left-to-right: every visual edge source must be at least one column left of target.
  for (let pass = 0; pass < Math.max(nodes.length, 1) * 2; pass += 1) {
    let changed = false;
    lineageEdges.forEach((edge) => {
      if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target)) return;
      const sourceStage = stages.get(edge.source) ?? 0;
      const targetStage = stages.get(edge.target) ?? 0;
      if (targetStage <= sourceStage) {
        stages.set(edge.target, sourceStage + 1);
        changed = true;
      }
    });
    if (!changed) break;
  }

  const nonUsageStages = visibleNodes
    .filter((node) => !isUsageLike(node))
    .map((node) => stages.get(node.id) ?? 0);
  const terminalUsageStage = (nonUsageStages.length ? Math.max(...nonUsageStages) : 0) + 1;
  visibleNodes.filter((node) => isUsageLike(node)).forEach((usage) => {
    const upstreamStages = lineageEdges
      .filter((edge) => edge.target === usage.id)
      .map((edge) => (stages.get(edge.source) ?? 0) + 1);
    stages.set(usage.id, Math.max(terminalUsageStage, stages.get(usage.id) ?? terminalUsageStage, ...upstreamStages));
  });

  const minStage = Math.min(...Array.from(stages.values()));
  const normalized = new Map(Array.from(stages.entries()).map(([id, stage]) => [id, stage - minStage]));
  const columns = new Map<number, LineageNode[]>();

  visibleNodes.forEach((node) => {
    const stage = normalized.get(node.id) ?? 0;
    const list = columns.get(stage) || [];
    list.push(node);
    columns.set(stage, list);
  });

  const next: Record<string, LineagePosition> = {};
  const stageX = new Map<number, number>();
  let nextX = 0;
  Array.from(columns.keys()).sort((a, b) => a - b).forEach((stage) => {
    stageX.set(stage, nextX);
    const branchPressure = lineageEdges.filter((edge) => (normalized.get(edge.source) ?? 0) === stage).length;
    nextX += Math.max(HORIZONTAL_SPACING, 470) + Math.min(220, branchPressure * 16);
  });
  Array.from(columns.entries()).sort((a, b) => a[0] - b[0]).forEach(([stage, column]) => {
    const sorted = [...column].sort((a, b) => {
      const roleDiff = roleSortWeight(a) - roleSortWeight(b);
      return roleDiff || String(a.path || a.label).localeCompare(String(b.path || b.label));
    });
    sorted.forEach((node, index) => {
      const centered = index - (sorted.length - 1) / 2;
      const neighborYs = [
        ...lineageEdges.filter((edge) => edge.source === node.id).map((edge) => previous[edge.target]?.y),
        ...lineageEdges.filter((edge) => edge.target === node.id).map((edge) => previous[edge.source]?.y),
      ].filter((value): value is number => typeof value === "number");
      const anchoredY = neighborYs.length
        ? neighborYs.reduce((sum, value) => sum + value, 0) / neighborYs.length + centered * (VERTICAL_SPACING * 0.24)
        : undefined;
      next[node.id] = {
        x: stageX.get(stage) ?? stage * HORIZONTAL_SPACING,
        y: previous[node.id]?.y ?? anchoredY ?? centered * VERTICAL_SPACING,
      };
    });
  });

  Array.from(columns.keys()).forEach((stage) => {
    const ids = visibleNodes
      .filter((node) => (normalized.get(node.id) ?? 0) === stage)
      .map((node) => node.id)
      .filter((id) => next[id])
      .sort((a, b) => next[a].y - next[b].y);
    let nextFreeY = -Infinity;
    ids.forEach((id) => {
      const y = Math.max(next[id].y, nextFreeY);
      next[id] = { ...next[id], y };
      nextFreeY = y + CARD_HEIGHT + 22;
    });
  });

  // Hidden children inherit their parent position so highlighting/path math remains stable.
  nodes.forEach((node) => {
    const parent = parentByChild.get(node.id);
    if (parent && next[parent]) {
      next[node.id] = { ...next[parent] };
    }
  });

  return next;
}
