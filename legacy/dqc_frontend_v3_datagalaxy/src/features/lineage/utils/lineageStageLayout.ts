import type { LineageDirection, LineageEdge, LineageNode, LineagePosition } from "../types/lineage.types";
import { HORIZONTAL_SPACING, VERTICAL_SPACING } from "./lineageLayout";
import { classifyLineageStage, roleSortWeight } from "./lineageStageClassifier";

const STAGE_ORDER: Record<string, number> = {
  golden_source: 0,
  source_asset: 0,
  data_processing: 1,
  data_processing_item: 1,
  field: 2,
  structure: 2,
  asset: 2,
  dataset: 2,
  table: 2,
  final_asset: 3,
  control: 4,
  usage: 5,
};

const STAGE_NUDGE = HORIZONTAL_SPACING * 0.18;

function stageIndex(node: Pick<LineageNode, "category" | "type">): number {
  const role = classifyLineageStage(node);
  if (role === "golden_source") return STAGE_ORDER.golden_source;
  if (role === "source_asset") return STAGE_ORDER.source_asset;
  if (role === "data_processing") return STAGE_ORDER.data_processing;
  if (role === "data_processing_item") return STAGE_ORDER.data_processing_item;
  if (role === "final_asset") return STAGE_ORDER.final_asset;
  if (role === "usage") return STAGE_ORDER.usage;
  if (role === "control") return STAGE_ORDER.control;
  const category = String(node.category || "").toLowerCase();
  return STAGE_ORDER[category] ?? STAGE_ORDER.asset;
}

function isProcessing(node?: LineageNode) {
  return node && classifyLineageStage(node) === "data_processing";
}

function isProcessingItem(node?: LineageNode) {
  return node && classifyLineageStage(node) === "data_processing_item";
}

function isUsage(node?: LineageNode) {
  return node && classifyLineageStage(node) === "usage";
}

function buildProcessingParentMap(nodes: LineageNode[], edges: LineageEdge[]) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const parentByChild = new Map<string, string>();
  edges.forEach((edge) => {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    const type = String(edge.type || edge.raw_type || "").toUpperCase();
    if (!type.includes("PART_OF") && !type.includes("PROCESSING_CONTEXT")) return;
    if (isProcessing(source) && isProcessingItem(target)) parentByChild.set(target.id, source.id);
    if (isProcessing(target) && isProcessingItem(source)) parentByChild.set(source.id, target.id);
  });
  return parentByChild;
}

export function stageAwareExpansionPositions(
  clicked: LineageNode,
  incoming: LineageNode[],
  direction: LineageDirection,
  clickedPosition: LineagePosition
): LineagePosition[] {
  const nextDepth = clicked.depth + (direction === "downstream" ? 1 : -1);
  const byStage = new Map<number, LineageNode[]>();

  incoming.forEach((node) => {
    const stage = stageIndex(node);
    const list = byStage.get(stage) || [];
    list.push(node);
    byStage.set(stage, list);
  });

  const lanes = [...byStage.entries()].sort((a, b) => a[0] - b[0]);
  const laneGap = VERTICAL_SPACING * 1.05;
  const laneStart = clickedPosition.y - ((lanes.length - 1) * laneGap) / 2;
  const lookup: Record<string, LineagePosition> = {};

  lanes.forEach(([stage, nodes], laneIndex) => {
    const baseY = laneStart + laneIndex * laneGap;
    nodes.forEach((node, nodeIndex) => {
      const centered = nodeIndex - (nodes.length - 1) / 2;
      const xDirection = direction === "downstream" ? 1 : -1;
      lookup[node.id] = {
        x: nextDepth * HORIZONTAL_SPACING + xDirection * stage * STAGE_NUDGE,
        y: baseY + centered * (VERTICAL_SPACING * 0.84),
      };
    });
  });

  return incoming.map((node) => lookup[node.id] || { x: nextDepth * HORIZONTAL_SPACING, y: clickedPosition.y });
}

export function computeStagePositions(
  nodes: LineageNode[],
  edges: LineageEdge[],
  previous: Record<string, LineagePosition> = {}
): Record<string, LineagePosition> {
  if (!nodes.length) return {};
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const processingParentByChild = buildProcessingParentMap(nodes, edges);
  const resolveVisualNodeId = (id: string) => processingParentByChild.get(id) || id;
  const stages = new Map(nodes.map((node) => [node.id, stageIndex(node)]));
  const lineageEdges = edges
    .filter((edge) => !String(edge.type || "").toUpperCase().includes("PART_OF"))
    .map((edge) => ({
      ...edge,
      source: resolveVisualNodeId(edge.visual_source || edge.source),
      target: resolveVisualNodeId(edge.visual_target || edge.target),
    }))
    .filter((edge) => byId.has(edge.source) && byId.has(edge.target) && edge.source !== edge.target);

  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    lineageEdges.forEach((edge) => {
      const sourceStage = stages.get(edge.source) ?? 0;
      const targetStage = stages.get(edge.target) ?? 0;
      if (targetStage <= sourceStage) {
        stages.set(edge.target, sourceStage + 1);
        changed = true;
      }
    });
    if (!changed) break;
  }

  nodes.forEach((node) => {
    if (!isUsage(node)) return;
    const inboundMax = lineageEdges
      .filter((edge) => edge.target === node.id)
      .reduce((max, edge) => Math.max(max, stages.get(edge.source) ?? 0), 0);
    stages.set(node.id, Math.max(stages.get(node.id) ?? 0, inboundMax + 1));
  });

  nodes.forEach((node) => {
    if (isUsage(node)) return;
    const targetsUsage = lineageEdges.some((edge) => edge.source === node.id && isUsage(byId.get(edge.target)));
    if (targetsUsage) {
      stages.set(node.id, Math.max(stages.get(node.id) ?? 0, STAGE_ORDER.final_asset));
    }
  });

  const minStage = Math.min(...[...stages.values()]);
  const normalized = new Map([...stages.entries()].map(([id, stage]) => [id, stage - minStage]));
  const columns = new Map<number, LineageNode[]>();
  nodes.forEach((node) => {
    const stage = normalized.get(node.id) ?? 0;
    const list = columns.get(stage) || [];
    list.push(node);
    columns.set(stage, list);
  });

  const next: Record<string, LineagePosition> = {};
  [...columns.entries()].forEach(([stage, column]) => {
    const sorted = [...column].sort((a, b) => {
      const roleDiff = roleSortWeight(a) - roleSortWeight(b);
      if (roleDiff !== 0) return roleDiff;
      return String(a.path || a.label).localeCompare(String(b.path || b.label));
    });
    sorted.forEach((node, index) => {
      const centered = index - (sorted.length - 1) / 2;
      next[node.id] = {
        x: stage * HORIZONTAL_SPACING,
        y: previous[node.id]?.y ?? centered * VERTICAL_SPACING * 1.24,
      };
    });
  });

  return next;
}
