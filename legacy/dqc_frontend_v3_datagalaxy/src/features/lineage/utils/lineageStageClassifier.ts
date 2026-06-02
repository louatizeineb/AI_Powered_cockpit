import type { LineageNode } from "../types/lineage.types";

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
  ]
    .filter(Boolean)
    .join(" ")
    .replace(/[_-]/g, " ")
    .toLowerCase();
}

export function classifyLineageStage(node: Partial<LineageNode>): LineageStageRole {
  const text = textFor(node);
  const category = String(node.category || "").toLowerCase();

  if (text.includes("usage") || text.includes("dashboard") || text.includes("report")) return "usage";
  if (text.includes("control") || text.includes("quality")) return "control";
  if (text.includes("data processing item") || text.includes("processing item") || text.includes("dataprocessingitem")) {
    return "data_processing_item";
  }
  if (text.includes("data processing") || text.includes("dataprocessing") || text.includes("traitement") || text.includes("process")) {
    return "data_processing";
  }
  if (text.includes("golden source")) return "golden_source";
  if (category === "source" || text.includes("source") || text.includes("database") || text.includes("filestore")) {
    return "source_asset";
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
