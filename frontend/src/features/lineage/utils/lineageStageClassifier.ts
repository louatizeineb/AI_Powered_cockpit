import type { LineageEdge, LineageNode } from "../types/lineage.types";

export type LineageStageRole =
  | "golden_source"
  | "source_asset"
  | "data_processing"
  | "data_processing_item"
  | "intermediate_asset"
  | "final_asset"
  | "usage"
  | "control";

function textFor(node: Partial<LineageNode>) {
  return [
    node.visual_role,
    node.category,
    node.type,
    node.entity_type,
    node.data_type,
    node.path_type,
    node.group_type,
    node.path_full,
    node.path,
  ]
    .filter(Boolean)
    .join(" ")
    .replace(/[_-]/g, " ")
    .toLowerCase();
}

export function canonicalRelType(type: string | null | undefined) {
  const compact = String(type || "").replace(/[\s_-]+/g, "").toUpperCase();
  const map: Record<string, string> = {
    ISINPUTOF: "IS_INPUT_OF",
    ISOUTPUTOF: "IS_OUTPUT_OF",
    ISUSEDBY: "IS_USED_BY",
    ISCALLEDBY: "IS_CALLED_BY",
    PARTOF: "PART_OF",
    PROCESSINGCONTEXT: "PROCESSING_CONTEXT",
    FLOWSTO: "FLOWS_TO",
    USES: "USES",
  };
  return map[compact] || String(type || "RELATED").replace(/[\s-]+/g, "_").toUpperCase();
}

export function classifyLineageStage(
  node: Partial<LineageNode>,
  graph?: { nodes: LineageNode[]; edges: LineageEdge[] }
): LineageStageRole {
  const text = textFor(node);
  const category = String(node.category || "").toLowerCase();
  const type = String(node.type || "").toLowerCase();

  if (text.includes("usage") || text.includes("dashboard") || text.includes("report") || category === "usage") {
    return "usage";
  }
  if (text.includes("control") || text.includes("quality") || text.includes("kqi") || category === "control") {
    return "control";
  }
  if (
    text.includes("data processing item") ||
    text.includes("processing item") ||
    text.includes("dataprocessingitem") ||
    category === "processing_item"
  ) {
    return "data_processing_item";
  }
  if (
    text.includes("data processing") ||
    text.includes("dataprocessing") ||
    text.includes("traitement") ||
    text.includes("process") ||
    category === "processing"
  ) {
    return "data_processing";
  }
  if (text.includes("golden source") || String(node.properties?.is_golden_source || "").toLowerCase() === "true") {
    return "golden_source";
  }
  if (category === "source" || text.includes("source") || text.includes("database") || text.includes("filestore")) {
    return "source_asset";
  }

  if (graph && node.id) {
    const isConsumed = graph.edges.some((edge) => edge.source === node.id && ["IS_INPUT_OF", "FLOWS_TO"].includes(canonicalRelType(edge.type)));
    const producesUsage = graph.edges.some((edge) => edge.source === node.id && classifyLineageStage(graph.nodes.find((n) => n.id === edge.target) || {}) === "usage");
    if (producesUsage) return "final_asset";
    if (isConsumed && (category === "field" || category === "structure" || category === "dataset" || type.includes("field"))) {
      return "intermediate_asset";
    }
  }

  if (text.includes("final asset")) return "final_asset";
  return "intermediate_asset";
}

export function roleSortWeight(node: Partial<LineageNode>): number {
  const role = classifyLineageStage(node);
  if (role === "golden_source") return 0;
  if (role === "source_asset") return 1;
  if (role === "data_processing") return 2;
  if (role === "data_processing_item") return 3;
  if (role === "intermediate_asset") return 4;
  if (role === "final_asset") return 5;
  if (role === "control") return 6;
  return 7;
}

export function isProcessingLike(node?: Partial<LineageNode>) {
  if (!node) return false;
  const role = classifyLineageStage(node);
  return role === "data_processing";
}

export function isProcessingItemLike(node?: Partial<LineageNode>) {
  if (!node) return false;
  const role = classifyLineageStage(node);
  return role === "data_processing_item";
}

export function isUsageLike(node?: Partial<LineageNode>) {
  if (!node) return false;
  return classifyLineageStage(node) === "usage";
}
