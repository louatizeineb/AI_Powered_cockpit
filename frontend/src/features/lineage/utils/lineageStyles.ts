import type { LineageNode } from "../types/lineage.types";

export const NODE_COLORS: Record<string, string> = {
  Field: "#4BA3FF",
  Structure: "#4BA3FF",
  Table: "#4BA3FF",
  Dataset: "#11A36A",
  Usage: "#12A36A",
  DataProcessing: "#315CFF",
  DataProcessingItem: "#315CFF",
  Source: "#7C3AED",
  Control: "#F59E0B",
};

export function colorForNode(node: LineageNode): string {
  const category = String(node.category || "").toLowerCase();
  if (category === "usage" || category === "dataset") return NODE_COLORS.Usage;
  if (category === "processing" || category === "processing_item") return NODE_COLORS.DataProcessing;
  if (category === "source") return NODE_COLORS.Source;
  if (category === "control") return NODE_COLORS.Control;
  return NODE_COLORS[node.type] || NODE_COLORS.Field;
}

export function iconForNode(node: LineageNode): string {
  const category = String(node.category || "").toLowerCase();
  if (category === "usage") return "USE";
  if (category === "processing" || category === "processing_item") return "PRC";
  if (category === "source") return "SRC";
  if (category === "control") return "KQI";
  if (category === "dataset") return "DTS";
  if (category === "field") return "FLD";
  return "TBL";
}

export function typeLabelForNode(node: LineageNode): string {
  const category = String(node.category || "").toLowerCase();
  if (category === "processing") return "Data Processing";
  if (category === "processing_item") return "Processing Step";
  if (category === "usage") return "Usage";
  if (category === "dataset") return "Dataset";
  if (category === "source") return "Source";
  if (category === "control") return "Control";
  if (category === "field") return "Field";
  if (category === "structure") return "Table";
  return node.type || "Asset";
}
