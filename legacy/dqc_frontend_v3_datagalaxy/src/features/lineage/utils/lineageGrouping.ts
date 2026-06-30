import type { LineageEdge, LineageNode } from "../types/lineage.types";

export type GroupedChildItem = {
  id: string;
  label: string;
  role?: string;
  nodeId?: string;
  hasUpstream?: boolean;
  hasDownstream?: boolean;
  highlightColor?: string | null;
};

function toItems(value: unknown): GroupedChildItem[] {
  if (!Array.isArray(value)) return [];
  const items = value.map((item, index) => {
      if (typeof item === "string") {
        return { id: `item-${index}-${item}`, label: item };
      }
      if (item && typeof item === "object") {
        const source = item as Record<string, unknown>;
        const label = String(source.label || source.name || source.technical_name || source.node_id || "").trim();
        if (!label) return null;
        return {
          id: String(source.id || source.node_id || `item-${index}-${label}`),
          label,
          role: String(source.role || source.type || source.category || ""),
          nodeId: String(source.id || source.node_id || ""),
          hasUpstream: Boolean(source.has_upstream),
          hasDownstream: Boolean(source.has_downstream),
        };
      }
      return null;
    }) as Array<GroupedChildItem | null>;
  return items.filter((item): item is GroupedChildItem => Boolean(item)).slice(0, 10);
}

export function groupedChildrenForNode(node: LineageNode): GroupedChildItem[] {
  const category = String(node.category || "").toLowerCase();
  const properties = node.properties || {};
  const likelyArrays = [
    properties.relevant_children,
    properties.relevant_fields,
    properties.fields,
    properties.children,
    properties.dpi_items,
    properties.processing_items,
    properties.visible_items,
  ];

  for (const candidate of likelyArrays) {
    const mapped = toItems(candidate);
    if (mapped.length) return mapped;
  }

  if (category === "processing" || category === "processing_item") {
    const primary = String(
      node.label || properties.dpi_name || properties.processing_item_name || properties.step_name || ""
    ).trim();
    if (primary) {
      return [{
        id: `${node.id}-dpi`,
        nodeId: node.id,
        label: primary,
        role: "DPI",
        hasUpstream: node.has_upstream,
        hasDownstream: node.has_downstream,
      }];
    }
  }

  if (["source", "structure", "field", "dataset"].includes(category)) {
    const primary = String(
      node.label || properties.field_name || properties.attribute_name || properties.column_name || ""
    ).trim();
    if (primary) {
      return [{
        id: `${node.id}-field`,
        nodeId: node.id,
        label: primary,
        role: category === "field" ? "Field" : "Asset",
        hasUpstream: node.has_upstream,
        hasDownstream: node.has_downstream,
      }];
    }
  }

  return [];
}

function isProcessing(node: LineageNode) {
  const category = String(node.category || "").toLowerCase();
  return category === "processing" || String(node.type || "").toLowerCase().includes("processing");
}

function isProcessingItem(node: LineageNode) {
  const category = String(node.category || "").toLowerCase();
  return category === "processing_item" || String(node.type || "").toLowerCase().includes("processingitem");
}

function isField(node: LineageNode) {
  const category = String(node.category || "").toLowerCase();
  return category === "field" || String(node.type || "").toLowerCase().includes("field");
}

function isFieldParent(node: LineageNode) {
  const category = String(node.category || "").toLowerCase();
  return ["source", "structure", "dataset"].includes(category) || /table|structure|dataset/i.test(String(node.type || ""));
}

function isParentChildEdge(edge: LineageEdge) {
  const type = String(edge.type || edge.raw_type || "").toUpperCase();
  return type.includes("PART_OF") || type.includes("HAS_FIELD") || type.includes("HAS_COLUMN");
}

export function buildGroupingFromGraph(
  nodes: LineageNode[],
  edges: LineageEdge[],
  highlightColorByNodeId: Record<string, string | null>
): {
  groupedByParentId: Record<string, GroupedChildItem[]>;
  hiddenNodeIds: Record<string, boolean>;
  parentByChildId: Record<string, string>;
} {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const groupedByParentId: Record<string, GroupedChildItem[]> = {};
  const hiddenNodeIds: Record<string, boolean> = {};
  const parentByChildId: Record<string, string> = {};

  edges.forEach((edge) => {
    const left = byId.get(edge.source);
    const right = byId.get(edge.target);
    if (!left || !right) return;

    let parent: LineageNode | null = null;
    let child: LineageNode | null = null;
    if (isProcessing(left) && isProcessingItem(right)) {
      parent = left;
      child = right;
    } else if (isProcessing(right) && isProcessingItem(left)) {
      parent = right;
      child = left;
    } else if (isParentChildEdge(edge) && isFieldParent(left) && isField(right)) {
      parent = left;
      child = right;
    } else if (isParentChildEdge(edge) && isFieldParent(right) && isField(left)) {
      parent = right;
      child = left;
    }
    if (!parent || !child) return;

    const list = groupedByParentId[parent.id] || [];
    if (!list.some((item) => item.nodeId === child.id)) {
      list.push({
        id: `${parent.id}:${child.id}`,
        nodeId: child.id,
        label: child.label || child.technical_name || child.node_id,
        role: isProcessingItem(child) ? "DPI" : "Field",
        hasUpstream: child.has_upstream,
        hasDownstream: child.has_downstream,
        highlightColor: highlightColorByNodeId[child.id] || null,
      });
    }
    groupedByParentId[parent.id] = list;
    hiddenNodeIds[child.id] = true;
    parentByChildId[child.id] = parent.id;
  });

  nodes.forEach((node) => {
    if (!groupedByParentId[node.id] || groupedByParentId[node.id].length === 0) {
      const fallback = groupedChildrenForNode(node).map((item) => ({
        ...item,
        highlightColor: item.nodeId ? highlightColorByNodeId[item.nodeId] || null : null,
      }));
      if (fallback.length) groupedByParentId[node.id] = fallback;
    }
  });

  Object.values(groupedByParentId).forEach((items) => {
    items.sort((a, b) => a.label.localeCompare(b.label));
  });

  return { groupedByParentId, hiddenNodeIds, parentByChildId };
}
