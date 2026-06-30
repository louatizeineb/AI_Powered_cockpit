import type { LineageEdge, LineageNode } from "../types/lineage.types";
import { canonicalRelType, isProcessingItemLike, isProcessingLike } from "./lineageStageClassifier";

export type GroupedChildItem = {
  id: string;
  label: string;
  role?: string;
  section?: string;
  linkedTo?: string[];
  nodeId?: string;
  hasUpstream?: boolean;
  hasDownstream?: boolean;
  highlightColor?: string | null;
  catalog?: boolean;
  depth?: number;
  children?: GroupedChildItem[];
};

function text(value: unknown) {
  return String(value || "").trim();
}

function flag(value: unknown) {
  return value === true || value === 1 || String(value).toLowerCase() === "true";
}

function splitPath(path: string | null | undefined) {
  return text(path)
    .split(/[\\/>|]+/g)
    .map((part) => part.trim())
    .filter(Boolean);
}

function parentPathKey(node: LineageNode) {
  const parts = splitPath(node.path || node.path_full || String(node.properties?.path || ""));
  if (parts.length <= 1) return "";
  return parts.slice(0, -1).join("/").toLowerCase();
}

function nodePathKey(node: LineageNode) {
  const parts = splitPath(node.path || node.path_full || String(node.properties?.path || ""));
  return parts.join("/").toLowerCase();
}

function toItems(value: unknown): GroupedChildItem[] {
  if (!Array.isArray(value)) return [];
  const items = value.map((item, index) => {
    if (typeof item === "string") return { id: `item-${index}-${item}`, label: item };
    if (item && typeof item === "object") {
      const source = item as Record<string, unknown>;
      const label = text(source.label || source.name || source.technical_name || source.node_id);
      if (!label) return null;
      return {
        id: text(source.id || source.node_id || `item-${index}-${label}`),
        label,
        role: text(source.role || source.type || source.category),
        nodeId: text(source.id || source.node_id),
        hasUpstream: flag(source.has_upstream),
        hasDownstream: flag(source.has_downstream),
      };
    }
    return null;
  }) as Array<GroupedChildItem | null>;
  return items.filter((item): item is GroupedChildItem => Boolean(item)).slice(0, 10);
}

export function groupedChildrenForNode(node: LineageNode): GroupedChildItem[] {
  const category = text(node.category).toLowerCase();
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

  if (category === "processing_item") {
    return [{
      id: `${node.id}-dpi`,
      nodeId: node.id,
      label: node.label || node.technical_name || node.node_id,
      role: "DPI",
      hasUpstream: node.has_upstream,
      hasDownstream: node.has_downstream,
    }];
  }

  if (["field", "structure", "dataset", "asset"].includes(category)) {
    return [{
      id: `${node.id}-field`,
      nodeId: node.id,
      label: node.label || node.technical_name || node.node_id,
      role: category === "field" ? "Field" : "Asset",
      hasUpstream: node.has_upstream,
      hasDownstream: node.has_downstream,
    }];
  }

  return [];
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

function isParentChildEdge(edge: LineageEdge) {
  const type = canonicalRelType(edge.type || edge.raw_type);
  return type.includes("PART_OF") || type.includes("PROCESSING_CONTEXT") || type.includes("HAS_FIELD") || type.includes("HAS_COLUMN") || type.includes("HAS_STRUCTURE") || type.includes("HAS_CONTAINER") || type.includes("CONTAINS");
}

function isCatalogHierarchyEdge(edge: LineageEdge) {
  const type = canonicalRelType(edge.type || edge.raw_type);
  return type.includes("HAS_FIELD") || type.includes("HAS_COLUMN") || type.includes("HAS_STRUCTURE") || type.includes("HAS_CONTAINER") || type.includes("CONTAINS");
}

function isStructure(node: LineageNode) {
  const category = text(node.category).toLowerCase();
  const type = text(node.type).toLowerCase();
  return category === "structure" || type.includes("structure") || type.includes("table");
}

function isContainer(node: LineageNode) {
  const category = text(node.category).toLowerCase();
  const type = text(node.type).toLowerCase();
  return category === "asset" && (type.includes("container") || type.includes("directory"));
}

function isUsage(node: LineageNode) {
  return text(node.category).toLowerCase() === "usage" || text(node.type).toLowerCase().includes("usage");
}

function addChild(
  groupedByParentId: Record<string, GroupedChildItem[]>,
  hiddenNodeIds: Record<string, boolean>,
  parentByChildId: Record<string, string>,
  parent: LineageNode,
  child: LineageNode,
  role: string,
  highlightColorByNodeId: Record<string, string | null>,
  section?: string
) {
  if (parent.id === child.id) return;
  if (parentByChildId[child.id] && parentByChildId[child.id] !== parent.id) return;
  const list = groupedByParentId[parent.id] || [];
  if (!list.some((item) => item.nodeId === child.id)) {
    list.push({
      id: `${parent.id}:${child.id}`,
      nodeId: child.id,
      label: child.label || child.technical_name || child.node_id,
      role,
      section,
      hasUpstream: child.has_upstream,
      hasDownstream: child.has_downstream,
      highlightColor: highlightColorByNodeId[child.id] || null,
    });
  }
  groupedByParentId[parent.id] = list;
  hiddenNodeIds[child.id] = true;
  parentByChildId[child.id] = parent.id;
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
  const catalogChildrenByParentId: Record<string, LineageNode[]> = {};
  const catalogItemByNodeId = new Map<string, GroupedChildItem>();

  edges.forEach((edge) => {
    if (!isCatalogHierarchyEdge(edge)) return;
    const left = byId.get(edge.source);
    const right = byId.get(edge.target);
    if (!left || !right) return;
    const parent = isCatalogParent(left) && (isCatalogParent(right) || isField(right))
      ? left
      : isCatalogParent(right) && (isCatalogParent(left) || isField(left))
        ? right
        : undefined;
    const child = parent?.id === left.id ? right : parent?.id === right.id ? left : undefined;
    if (!parent || !child) return;
    const list = catalogChildrenByParentId[parent.id] || [];
    if (!list.some((item) => item.id === child.id)) list.push(child);
    catalogChildrenByParentId[parent.id] = list;
  });

  function catalogRole(node: LineageNode) {
    if (isContainer(node)) return "Container";
    if (isStructure(node)) return "Structure";
    if (isUsage(node)) return "Usage";
    return "Field";
  }

  function buildCatalogItem(node: LineageNode, source: LineageNode, seen: Set<string>): GroupedChildItem {
    const nextSeen = new Set(seen);
    nextSeen.add(node.id);
    hiddenNodeIds[node.id] = true;
    parentByChildId[node.id] = source.id;
    const children = (catalogChildrenByParentId[node.id] || [])
      .filter((child) => !nextSeen.has(child.id))
      .map((child) => buildCatalogItem(child, source, nextSeen));
    const item: GroupedChildItem = {
      id: `${source.id}:${node.id}`,
      nodeId: node.id,
      label: node.label || node.technical_name || node.node_id,
      role: catalogRole(node),
      catalog: true,
      hasUpstream: node.has_upstream,
      hasDownstream: node.has_downstream,
      highlightColor: highlightColorByNodeId[node.id] || null,
      children,
    };
    catalogItemByNodeId.set(node.id, item);
    return item;
  }

  const catalogChildIds = new Set(
    Object.values(catalogChildrenByParentId).flat().map((child) => child.id)
  );
  nodes.filter((node) => isCatalogParent(node) && !catalogChildIds.has(node.id)).forEach((root) => {
    const rows = (catalogChildrenByParentId[root.id] || []).map((child) =>
      buildCatalogItem(child, root, new Set([root.id]))
    );
    if (rows.length) groupedByParentId[root.id] = rows;
  });

  // 1) Explicit grouping edges returned by the backend.
  edges.forEach((edge) => {
    const left = byId.get(edge.source);
    const right = byId.get(edge.target);
    if (!left || !right) return;

    if (isProcessingLike(left) && isProcessingItemLike(right)) {
      addChild(groupedByParentId, hiddenNodeIds, parentByChildId, left, right, "DPI", highlightColorByNodeId);
      return;
    }
    if (isProcessingLike(right) && isProcessingItemLike(left)) {
      addChild(groupedByParentId, hiddenNodeIds, parentByChildId, right, left, "DPI", highlightColorByNodeId);
      return;
    }
    if (isParentChildEdge(edge) && isFieldParent(left) && isField(right)) {
      addChild(groupedByParentId, hiddenNodeIds, parentByChildId, left, right, "Field", highlightColorByNodeId);
      return;
    }
    if (isParentChildEdge(edge) && isFieldParent(right) && isField(left)) {
      addChild(groupedByParentId, hiddenNodeIds, parentByChildId, right, left, "Field", highlightColorByNodeId);
    }
  });

  // 2) Heuristic DP/DPI grouping when the link table returned both DP and DPI but no explicit PART_OF edge.
  const processingNodes = nodes.filter((node) => isProcessingLike(node));
  const processingItems = nodes.filter((node) => isProcessingItemLike(node));
  processingItems.forEach((item) => {
    if (parentByChildId[item.id]) return;
    const itemParentPath = parentPathKey(item);
    const parent = processingNodes.find((candidate) => {
      const candidateKey = nodePathKey(candidate);
      return item.parent_node_id === candidate.node_id || itemParentPath === candidateKey || itemParentPath.endsWith(`/${text(candidate.label).toLowerCase()}`);
    });
    if (parent) addChild(groupedByParentId, hiddenNodeIds, parentByChildId, parent, item, "DPI", highlightColorByNodeId);
  });

  // 3) Heuristic field grouping by parent node id/group id if the parent is already visible.
  nodes.filter(isField).forEach((field) => {
    if (parentByChildId[field.id]) return;
    const parent = nodes.find((candidate) => {
      if (!isFieldParent(candidate)) return false;
      return Boolean(
        field.parent_node_id && field.parent_node_id === candidate.node_id ||
        field.group_id && field.group_id === candidate.node_id ||
        field.group_id && field.group_id === candidate.id ||
        field.parent_label && field.parent_label === candidate.label
      );
    });
    if (parent) addChild(groupedByParentId, hiddenNodeIds, parentByChildId, parent, field, "Field", highlightColorByNodeId);
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

  edges.forEach((edge) => {
    if (isParentChildEdge(edge)) return;
    const source = byId.get(edge.visual_source || edge.source);
    const target = byId.get(edge.visual_target || edge.target);
    if (!source || !target) return;
    [
      [source, target],
      [target, source],
    ].forEach(([child, consumer]) => {
      if (!isField(child)) return;
      const row = catalogItemByNodeId.get(child.id);
      if (!row) return;
      const link = `${consumer.type}: ${consumer.label}`;
      row.linkedTo = [...new Set([...(row.linkedTo || []), link])];
    });
  });

  function sortItems(items: GroupedChildItem[]) {
    items.sort((a, b) => {
      const order = (role?: string) => role === "DPI" ? 0 : role === "Container" ? 1 : role === "Structure" ? 2 : role === "Field" ? 3 : 4;
      const diff = order(a.role) - order(b.role);
      return diff || a.label.localeCompare(b.label);
    });
    items.forEach((item) => sortItems(item.children || []));
  }

  Object.values(groupedByParentId).forEach(sortItems);

  return { groupedByParentId, hiddenNodeIds, parentByChildId };
}

export function visibleGroupedChildren(
  rows: GroupedChildItem[],
  expandedCatalogRows: Record<string, boolean>
) {
  const visible: GroupedChildItem[] = [];
  function append(items: GroupedChildItem[], depth: number) {
    items.forEach((item) => {
      visible.push({ ...item, depth });
      if (item.nodeId && item.catalog && expandedCatalogRows[item.nodeId]) {
        append(item.children || [], depth + 1);
      }
    });
  }
  append(rows, 0);
  return visible;
}

export function visibleCatalogRowByNodeId(
  rows: GroupedChildItem[],
  expandedCatalogRows: Record<string, boolean>
) {
  const visibleOwner: Record<string, string> = {};
  function assignCollapsed(items: GroupedChildItem[], owner?: string) {
    items.forEach((item) => {
      if (item.nodeId && owner) visibleOwner[item.nodeId] = owner;
      assignCollapsed(item.children || [], owner);
    });
  }
  function append(items: GroupedChildItem[], visibleParent?: string) {
    items.forEach((item) => {
      const owner = item.nodeId || visibleParent;
      if (item.nodeId && owner) visibleOwner[item.nodeId] = owner;
      if (item.nodeId && expandedCatalogRows[item.nodeId]) {
        append(item.children || [], item.nodeId);
      } else {
        assignCollapsed(item.children || [], owner);
      }
    });
  }
  append(rows);
  return visibleOwner;
}
