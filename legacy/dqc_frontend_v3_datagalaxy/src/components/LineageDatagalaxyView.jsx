import React, { useEffect, useMemo, useRef, useState } from "react";

const CARD_WIDTH = 292;
const COLUMN_GAP = 94;
const CARD_GAP = 28;
const BOARD_PADDING = 36;
const HEADER_HEIGHT = 78;
const ROW_HEIGHT = 32;
const FOOTER_HEIGHT = 34;
const DEFAULT_VISIBLE_ROWS = 10;
const MIN_AUTO_ZOOM = 0.68;
const MAX_AUTO_ZOOM = 1;

function cls(...items) {
  return items.filter(Boolean).join(" ");
}

function compact(value, max = 38) {
  const text = String(value || "");
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (value === undefined || value === null) return [];
  return [value];
}

function normalizeText(value) {
  return String(value || "").trim().toLowerCase();
}

function normalizeSlashes(value) {
  return String(value || "")
    .replace(/[\\/]+/g, "\\")
    .replace(/^\\+|\\+$/g, "")
    .trim();
}

function firstNonEmpty(...values) {
  return values.find((value) => value !== undefined && value !== null && String(value).trim() !== "");
}

function rawProps(raw) {
  return raw?.properties || raw?.props || {};
}

function edgeNodeId(value) {
  if (!value) return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  return String(value.id || value.node_id || value.nodeId || value.properties?.node_id || "");
}

function canonicalEdgeType(value) {
  const raw = String(value || "RELATED").trim();
  const compact = raw.replace(/[\s_-]/g, "").toUpperCase();
  if (compact === "ISINPUTOF") return "IS_INPUT_OF";
  if (compact === "ISOUTPUTOF") return "IS_OUTPUT_OF";
  if (compact === "PARTOF") return "PART_OF";
  if (compact === "FLOWSTO") return "FLOWS_TO";
  if (compact === "BELONGSTO") return "BELONGS_TO";
  if (compact === "HASFIELDS" || compact === "HASFIELD") return "HAS_FIELD";
  if (compact === "HASCOLUMNS" || compact === "HASCOLUMN") return "HAS_COLUMN";
  if (compact === "ISUSEDBY") return "IS_USED_BY";
  if (compact === "ISUSAGESOURCEFOR") return "IS_USAGE_SOURCE_FOR";
  if (compact === "ISUSAGEDESTINATIONFOR") return "IS_USAGE_DESTINATION_FOR";
  if (compact === "HASFORSOURCE") return "HAS_FOR_SOURCE";
  if (compact === "ISSOURCEOF") return "IS_SOURCE_OF";
  if (compact === "ISLINKEDTO") return "IS_LINKED_TO";
  if (compact === "ISCALLEDBY") return "IS_CALLED_BY";
  if (compact === "ISIMPLEMENTEDBY") return "IS_IMPLEMENTED_BY";
  if (compact === "ISPARTOFDIMENSION") return "IS_PART_OF_DIMENSION";
  if (compact === "HASFORUNIVERSE") return "HAS_FOR_UNIVERSE";
  if (compact === "ISUNIVERSEOF") return "IS_UNIVERSE_OF";
  if (compact === "USAGEPARENT") return "USAGE_PARENT";
  return raw.replace(/[\s-]+/g, "_").toUpperCase();
}

function allNodeAliases(node) {
  return [
    node?.id,
    node?.nodeId,
    node?.raw?.id,
    node?.raw?.node_id,
    node?.raw?.nodeId,
    node?.raw?.usage_uuid,
    node?.raw?.properties?.node_id,
    node?.raw?.properties?.usage_uuid,
    node?.path,
  ]
    .filter(Boolean)
    .map(String);
}

export function normalizePath(value) {
  return normalizeSlashes(value)
    .split("\\")
    .map((part) => part.trim())
    .filter(Boolean)
    .join("\\");
}

export function detectKind(raw) {
  const props = rawProps(raw);
  if (raw?.kind) return raw.kind;
  if (raw?.usage_uuid || props.usage_uuid || props.usage_name) return "usage";
  const terms = [
    ...asArray(raw?.labels),
    ...asArray(raw?.label && !raw?.name ? raw.label : undefined),
    raw?.type,
    raw?.entity_type,
    raw?.data_type,
    props.type,
    props.entity_type,
    props.data_type,
    props.label,
    props.labels,
  ]
    .flatMap((item) => asArray(item))
    .filter(Boolean)
    .join(" ")
    .replace(/[_-]/g, " ")
    .toLowerCase();

  const compact = terms.replace(/\s+/g, "");
  if (compact.includes("dataprocessingitem") || terms.includes("processing item")) return "data_processing_item";
  if (compact.includes("dataprocessing") || terms.includes("traitement") || terms.includes("process")) return "data_processing";
  if (terms.includes("field") || terms.includes("column") || terms.includes("attribut")) return "field";
  if (terms.includes("source")) return "source";
  if (terms.includes("container")) return "container";
  if (terms.includes("structure") || terms.includes("payload") || terms.includes("table")) return "structure";
  if (terms.includes("usage") || terms.includes("dashboard") || terms.includes("report") || terms.includes("application")) {
    return "usage";
  }
  return "unknown";
}

export function normalizeNode(raw) {
  const props = rawProps(raw);
  const id = String(firstNonEmpty(raw?.id, raw?.node_id, raw?.nodeId, raw?.usage_uuid, props.node_id, props.usage_uuid, props.id, props.path_full, props.path));
  const nodeId = String(firstNonEmpty(raw?.node_id, raw?.nodeId, props.node_id, id));
  const displayName = String(
    firstNonEmpty(
      raw?.usage_name,
      props.usage_name,
      raw?.name,
      raw?.label,
      raw?.name_label,
      props.name_label,
      props.name,
      props.label,
      props.name_tech,
      props.technical_name,
      props.field_name,
      props.structure_name,
      props.data_processing_name,
      props.usage_tech_name,
      nodeId,
      id,
      "Unnamed"
    )
  );
  const technicalName = String(firstNonEmpty(raw?.usage_tech_name, raw?.name_tech, raw?.technicalName, props.usage_tech_name, props.name_tech, props.technical_name, displayName));
  return {
    id,
    nodeId,
    displayName,
    technicalName,
    kind: detectKind(raw),
    labels: asArray(raw?.labels || props.labels).map(String),
    path: normalizePath(firstNonEmpty(raw?.usage_path, raw?.path_full, raw?.path, props.usage_path, props.path_full, props.path, props.technical_path, "")),
    parentNodeId: firstNonEmpty(raw?.parent_node_id, raw?.parentNodeId, props.parent_node_id, props.parentNodeId),
    quality: raw?.quality || props.quality || null,
    qualityChecks: raw?.quality_checks || props.quality_checks || [],
    raw,
  };
}

export function normalizeEdge(raw, index = 0) {
  const props = rawProps(raw);
  const source = edgeNodeId(firstNonEmpty(raw?.source, raw?.source_id, raw?.from, raw?.start, props.source, props.source_id));
  const target = edgeNodeId(firstNonEmpty(raw?.target, raw?.target_id, raw?.to, raw?.end, props.target, props.target_id));
  const type = canonicalEdgeType(firstNonEmpty(raw?.type, raw?.label, raw?.relationship, props.type, props.link_type, props.relationship_type, "RELATED"));
  return {
    id: String(firstNonEmpty(raw?.id, props.id, `${source}->${target}:${type}:${index}`)),
    source,
    target,
    type,
    properties: props,
    raw,
  };
}

function indexNodes(nodes) {
  const byId = new Map();
  nodes.forEach((node) => {
    allNodeAliases(node).forEach((alias) => byId.set(alias, node));
  });
  return byId;
}

function resolveNode(byId, id) {
  return byId.get(String(id || ""));
}

function relationIs(edge, ...names) {
  return names.includes(canonicalEdgeType(edge?.type || ""));
}

function isCardKind(kind) {
  return ["source", "container", "structure", "usage"].includes(kind);
}

function parentByPath(child, candidates) {
  if (!child.path) return null;
  const childPath = normalizeText(normalizePath(child.path));
  return candidates
    .filter((candidate) => candidate.path && childPath.startsWith(`${normalizeText(candidate.path)}\\`))
    .sort((left, right) => right.path.length - left.path.length)[0];
}

export function inferDpiParent(dpi, nodes, edges) {
  const byId = indexNodes(nodes);
  const explicitParent = dpi.parentNodeId ? resolveNode(byId, dpi.parentNodeId) : null;
  if (explicitParent?.kind === "data_processing") return explicitParent.id;

  for (const edge of edges) {
    if (!relationIs(edge, "PART_OF", "BELONGS_TO", "CONTAINS")) continue;
    const source = resolveNode(byId, edge.source);
    const target = resolveNode(byId, edge.target);
    if (source?.id === dpi.id && target?.kind === "data_processing") return target.id;
    if (target?.id === dpi.id && source?.kind === "data_processing") return source.id;
  }

  const inferred = parentByPath(
    dpi,
    nodes.filter((node) => node.kind === "data_processing")
  );
  return inferred?.id || null;
}

function inferFieldParent(field, nodes, edges) {
  const byId = indexNodes(nodes);
  const explicitParent = field.parentNodeId ? resolveNode(byId, field.parentNodeId) : null;
  if (explicitParent && isCardKind(explicitParent.kind)) return explicitParent.id;

  for (const edge of edges) {
    if (relationIs(edge, "IS_INPUT_OF", "IS_OUTPUT_OF", "FLOWS_TO", "LINKS_TO")) continue;
    const source = resolveNode(byId, edge.source);
    const target = resolveNode(byId, edge.target);
    if (source?.id === field.id && target && isCardKind(target.kind)) return target.id;
    if (target?.id === field.id && source && isCardKind(source.kind)) return source.id;
  }

  const inferred = parentByPath(
    field,
    nodes.filter((node) => isCardKind(node.kind))
  );
  return inferred?.id || null;
}

function iconForKind(kind) {
  if (kind === "data_processing" || kind === "data_processing_item") return "DP";
  if (kind === "source") return "FileStore";
  if (kind === "structure") return "TBL";
  if (kind === "container") return "BOX";
  if (kind === "usage") return "Usage";
  if (kind === "field") return "FLD";
  return "OBJ";
}

function cardBadge(kind) {
  if (kind === "data_processing") return "DP";
  if (kind === "source") return "Source";
  if (kind === "structure") return "Table";
  if (kind === "container") return "Struct.";
  if (kind === "usage") return "Usage";
  return "Object";
}

function createCard(node, override = {}) {
  return {
    id: override.id || `card:${node.id}`,
    kind: override.kind || node.kind,
    title: override.title || node.displayName,
    path: override.path ?? node.path,
    node,
    items: [],
    rawNodes: [node],
  };
}

function sortItems(items) {
  return [...items].sort((left, right) => {
    const leftRank = left.visualRank ?? 1;
    const rightRank = right.visualRank ?? 1;
    if (leftRank !== rightRank) return leftRank - rightRank;
    const leftPath = left.path || left.displayName;
    const rightPath = right.path || right.displayName;
    return leftPath.localeCompare(rightPath, undefined, { numeric: true, sensitivity: "base" });
  });
}

function inferStructureParent(structure, nodes, edges) {
  const byId = indexNodes(nodes);
  const explicitParent = structure.parentNodeId ? resolveNode(byId, structure.parentNodeId) : null;
  if (explicitParent && ["source", "container"].includes(explicitParent.kind)) return explicitParent.id;

  for (const edge of edges) {
    if (!relationIs(edge, "CONTAINS", "PART_OF", "HAS_STRUCTURE")) continue;
    const source = resolveNode(byId, edge.source);
    const target = resolveNode(byId, edge.target);
    if (target?.id === structure.id && source && ["source", "container"].includes(source.kind)) return source.id;
    if (source?.id === structure.id && target && ["source", "container"].includes(target.kind)) return target.id;
  }

  const inferred = parentByPath(
    structure,
    nodes.filter((node) => ["source", "container"].includes(node.kind))
  );
  return inferred?.id || null;
}

export function groupNodesIntoCards(rawNodes = [], rawEdges = []) {
  const nodes = rawNodes.map(normalizeNode).filter((node) => node.id && node.id !== "undefined");
  const edges = rawEdges.map(normalizeEdge).filter((edge) => edge.source && edge.target);
  const byId = indexNodes(nodes);
  const cards = new Map();
  const itemToCard = new Map();
  const structureParent = new Map();

  nodes.filter((node) => node.kind === "structure").forEach((node) => {
    const parentId = inferStructureParent(node, nodes, edges);
    if (parentId) structureParent.set(node.id, parentId);
  });

  nodes.filter((node) => ["source", "container", "usage"].includes(node.kind)).forEach((node) => {
    cards.set(node.id, createCard(node));
    itemToCard.set(node.id, node.id);
  });

  nodes.filter((node) => node.kind === "structure" && !structureParent.has(node.id)).forEach((node) => {
    cards.set(node.id, createCard(node));
    itemToCard.set(node.id, node.id);
  });

  nodes.filter((node) => node.kind === "structure" && structureParent.has(node.id)).forEach((node) => {
    const parentCard = cards.get(structureParent.get(node.id));
    if (!parentCard) return;
    parentCard.items.push({ ...node, visualDepth: 0, visualRank: 0 });
    parentCard.rawNodes.push(node);
    itemToCard.set(node.id, parentCard.id);
  });

  nodes.filter((node) => node.kind === "data_processing").forEach((node) => {
    cards.set(node.id, createCard(node, { kind: "data_processing" }));
    itemToCard.set(node.id, node.id);
  });

  const processingFallbackId = "fallback:data-processing";
  nodes.filter((node) => node.kind === "data_processing_item").forEach((node) => {
    let parentId = inferDpiParent(node, nodes, edges);
    if (!parentId) {
      if (!cards.has(processingFallbackId)) {
        const fallbackNode = {
          ...node,
          id: processingFallbackId,
          nodeId: processingFallbackId,
          displayName: "Processing",
          technicalName: "Processing",
          kind: "data_processing",
          path: "",
        };
        cards.set(processingFallbackId, createCard(fallbackNode, { id: processingFallbackId, kind: "data_processing" }));
      }
      parentId = processingFallbackId;
    }
    const card = cards.get(parentId);
    if (card) {
      card.items.push(node);
      card.rawNodes.push(node);
      itemToCard.set(node.id, card.id);
    }
  });

  nodes.filter((node) => node.kind === "field").forEach((node) => {
    let parentId = inferFieldParent(node, nodes, edges);
    if (parentId && structureParent.has(parentId)) parentId = structureParent.get(parentId);
    if (!parentId || !cards.has(parentId)) {
      const title = node.path ? node.path.split("\\").slice(0, -1).pop() || "Fields" : "Fields";
      parentId = `fallback:field:${node.id}`;
      cards.set(parentId, createCard(node, { id: parentId, kind: "structure", title, path: node.path }));
    }
    const card = cards.get(parentId);
    if (card) {
      const parentNode = node.parentNodeId ? resolveNode(byId, node.parentNodeId) : null;
      card.items.push({
        ...node,
        visualDepth: parentNode?.kind === "structure" && structureParent.has(parentNode.id) ? 1 : 0,
        visualRank: parentNode?.kind === "structure" && structureParent.has(parentNode.id) ? 1 : 0,
      });
      card.rawNodes.push(node);
      itemToCard.set(node.id, card.id);
    }
  });

  nodes.forEach((node) => {
    if (itemToCard.has(node.id)) return;
    if (!cards.has(node.id)) {
      cards.set(node.id, createCard(node, { kind: node.kind === "unknown" ? "container" : node.kind }));
    }
    itemToCard.set(node.id, node.id);
  });

  cards.forEach((card) => {
    card.items = sortItems(card.items);
    if (!card.items.length && card.kind === "data_processing") card.items = [card.node];
    if (!card.items.length && !isCardKind(card.node.kind)) card.items = [card.node];
  });

  return { nodes, edges, cards: [...cards.values()], itemToCard, nodeById: byId };
}

function isProcessing(node) {
  return node?.kind === "data_processing" || node?.kind === "data_processing_item";
}

function isFieldLike(node) {
  return node?.kind === "field" || node?.kind === "structure" || node?.kind === "source" || node?.kind === "container" || node?.kind === "usage";
}

function isLineageRelation(edge) {
  return relationIs(
    edge,
    "IS_INPUT_OF",
    "IS_OUTPUT_OF",
    "FLOWS_TO",
    "LINKS_TO",
    "RELATED",
    "USES",
    "IS_USED_BY",
    "IS_USAGE_SOURCE_FOR",
    "IS_USAGE_DESTINATION_FOR",
    "HAS_FOR_SOURCE",
    "IS_SOURCE_OF",
    "IS_LINKED_TO",
    "CALLS",
    "IS_CALLED_BY",
    "IMPLEMENTS",
    "IS_IMPLEMENTED_BY",
    "GENERALIZES",
    "SPECIALIZES",
    "REGROUPS",
    "IS_PART_OF_DIMENSION",
    "HAS_FOR_UNIVERSE",
    "IS_UNIVERSE_OF",
    "USAGE_PARENT",
    "USAGE_DEPENDS_ON",
    "SOURCE_USES_USAGE"
  );
}

function parentValue(node) {
  const props = rawProps(node?.raw);
  return firstNonEmpty(node?.parentNodeId, props.parent_uuid, props.parent_node_id, props.parent_id, props.parent_usage_uuid);
}

function isUsageParentRelation(edge) {
  return relationIs(
    edge,
    "GENERALIZES",
    "SPECIALIZES",
    "REGROUPS",
    "IS_PART_OF_DIMENSION",
    "HAS_FOR_UNIVERSE",
    "IS_UNIVERSE_OF",
    "USAGE_PARENT",
    "USAGE_DEPENDS_ON"
  );
}

export function deriveVisualLineageEdges(rawNodes = [], rawEdges = []) {
  const grouped = groupNodesIntoCards(rawNodes, rawEdges);
  const visualEdges = [];

  grouped.edges.forEach((edge) => {
    const source = resolveNode(grouped.nodeById, edge.source);
    const target = resolveNode(grouped.nodeById, edge.target);
    if (!source || !target || relationIs(edge, "PART_OF", "CONTAINS", "BELONGS_TO", "HAS_FIELD", "HAS_COLUMN")) return;
    if (!isLineageRelation(edge)) return;
    if (!isProcessing(source) && !isProcessing(target) && !(isFieldLike(source) && isFieldLike(target))) return;

    let visualSource = source.id;
    let visualTarget = target.id;
    if (source.kind === "usage" && target.kind === "usage" && isUsageParentRelation(edge)) {
      const sourceParent = String(parentValue(source) || "");
      const targetParent = String(parentValue(target) || "");
      if (sourceParent && allNodeAliases(target).includes(sourceParent)) {
        visualSource = target.id;
        visualTarget = source.id;
      } else if (targetParent && allNodeAliases(source).includes(targetParent)) {
        visualSource = source.id;
        visualTarget = target.id;
      }
    } else if (source.kind === "usage" && target.kind !== "usage") {
      visualSource = target.id;
      visualTarget = source.id;
    } else if (target.kind === "usage" && source.kind !== "usage") {
      visualSource = source.id;
      visualTarget = target.id;
    } else if (relationIs(edge, "IS_OUTPUT_OF")) {
      if (isFieldLike(source) && isProcessing(target)) {
        visualSource = target.id;
        visualTarget = source.id;
      } else if (isProcessing(source) && isFieldLike(target)) {
        visualSource = source.id;
        visualTarget = target.id;
      } else {
        visualSource = target.id;
        visualTarget = source.id;
      }
    }

    const viaDpi = edge.properties?.via_dpi || edge.properties?.via_dpi_id || edge.properties?.data_processing_item_id;
    const viaNode = viaDpi ? resolveNode(grouped.nodeById, viaDpi) : null;
    if (relationIs(edge, "FLOWS_TO") && viaNode) {
      visualEdges.push({
        id: `${edge.id}:via-in`,
        rawEdgeId: edge.id,
        source: visualSource,
        target: viaNode.id,
        type: edge.type,
      });
      visualEdges.push({
        id: `${edge.id}:via-out`,
        rawEdgeId: edge.id,
        source: viaNode.id,
        target: visualTarget,
        type: edge.type,
      });
      return;
    }

    visualEdges.push({
      id: edge.id,
      rawEdgeId: edge.id,
      source: visualSource,
      target: visualTarget,
      type: edge.type,
    });
  });

  return visualEdges;
}

function shortestPath(edges, startId, endId, undirected = false, preferredTypes = null) {
  if (!startId || !endId) return null;
  const adjacency = new Map();
  edges.forEach((edge) => {
    if (preferredTypes && !preferredTypes.has(edge.type)) return;
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
    adjacency.get(edge.source).push({ edge, next: edge.target });
    if (undirected) {
      if (!adjacency.has(edge.target)) adjacency.set(edge.target, []);
      adjacency.get(edge.target).push({ edge, next: edge.source });
    }
  });
  const queue = [{ nodeId: String(startId), nodes: [String(startId)], edgeIds: [] }];
  const seen = new Set([String(startId)]);
  while (queue.length) {
    const current = queue.shift();
    if (current.nodeId === String(endId)) return current;
    for (const step of adjacency.get(current.nodeId) || []) {
      if (seen.has(step.next)) continue;
      seen.add(step.next);
      queue.push({
        nodeId: step.next,
        nodes: [...current.nodes, step.next],
        edgeIds: [...current.edgeIds, step.edge.id, step.edge.rawEdgeId].filter(Boolean),
      });
    }
  }
  return null;
}

export function computeFocusedLineagePath({ nodes = [], edges = [], focusNodeId, startNodeId, endNodeId }) {
  const grouped = groupNodesIntoCards(nodes, edges);
  const visualEdges = deriveVisualLineageEdges(nodes, edges);
  const focus = resolveNode(grouped.nodeById, focusNodeId)?.id || String(focusNodeId || "");
  if (!focus) return [];

  const nodeIds = new Set(grouped.nodes.map((node) => node.id));
  const inDegree = new Map(grouped.nodes.map((node) => [node.id, 0]));
  const outDegree = new Map(grouped.nodes.map((node) => [node.id, 0]));
  visualEdges.forEach((edge) => {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return;
    outDegree.set(edge.source, (outDegree.get(edge.source) || 0) + 1);
    inDegree.set(edge.target, (inDegree.get(edge.target) || 0) + 1);
  });

  const roots = startNodeId
    ? [resolveNode(grouped.nodeById, startNodeId)?.id || String(startNodeId)]
    : grouped.nodes.filter((node) => (inDegree.get(node.id) || 0) === 0 && (outDegree.get(node.id) || 0) > 0).map((node) => node.id);
  const leaves = endNodeId
    ? [resolveNode(grouped.nodeById, endNodeId)?.id || String(endNodeId)]
    : grouped.nodes.filter((node) => (outDegree.get(node.id) || 0) === 0 && (inDegree.get(node.id) || 0) > 0).map((node) => node.id);

  const bestInbound = roots
    .map((root) => shortestPath(visualEdges, root, focus, false) || shortestPath(visualEdges, root, focus, true))
    .filter(Boolean)
    .sort((left, right) => left.nodes.length - right.nodes.length)[0];
  const bestOutbound = leaves
    .map((leaf) => shortestPath(visualEdges, focus, leaf, false) || shortestPath(visualEdges, focus, leaf, true))
    .filter(Boolean)
    .sort((left, right) => left.nodes.length - right.nodes.length)[0];

  const orderedNodes = bestInbound?.nodes?.length ? [...bestInbound.nodes] : [focus];
  (bestOutbound?.nodes || []).slice(1).forEach((id) => orderedNodes.push(id));
  const orderedEdges = [...(bestInbound?.edgeIds || []), ...(bestOutbound?.edgeIds || [])];
  const active = new Set([...orderedNodes, ...orderedEdges]);

  if (!bestInbound && !bestOutbound) {
    const adjacentEdges = [];
    visualEdges.forEach((edge) => {
      if (edge.source === focus || edge.target === focus) {
        adjacentEdges.push(edge);
        active.add(edge.id);
        if (edge.rawEdgeId) active.add(edge.rawEdgeId);
        active.add(edge.source);
        active.add(edge.target);
      }
    });
    return [
      focus,
      ...adjacentEdges.flatMap((edge) => [edge.source, edge.id, edge.rawEdgeId, edge.target]).filter(Boolean),
    ];
  }

  return [...orderedNodes, ...orderedEdges.filter((id) => active.has(id))];
}

function inferEndpoints(nodes, visualEdges, startNodeId, endNodeId) {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const inDegree = new Map(nodes.map((node) => [node.id, 0]));
  const outDegree = new Map(nodes.map((node) => [node.id, 0]));
  visualEdges.forEach((edge) => {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return;
    outDegree.set(edge.source, (outDegree.get(edge.source) || 0) + 1);
    inDegree.set(edge.target, (inDegree.get(edge.target) || 0) + 1);
  });
  const starts = nodes.filter((node) => (outDegree.get(node.id) || 0) > 0 && (inDegree.get(node.id) || 0) === 0);
  const ends = nodes.filter((node) => (inDegree.get(node.id) || 0) > 0 && (outDegree.get(node.id) || 0) === 0);
  return {
    start: String(startNodeId || starts.find((node) => node.kind === "field")?.id || starts[0]?.id || nodes[0]?.id || ""),
    end: String(endNodeId || ends.find((node) => node.kind === "field")?.id || ends[0]?.id || nodes[nodes.length - 1]?.id || ""),
  };
}

export function computeActivePath({ nodes = [], edges = [], activePath, startNodeId, endNodeId }) {
  const grouped = groupNodesIntoCards(nodes, edges);
  const visualEdges = deriveVisualLineageEdges(nodes, edges);
  const activeValues = new Set((activePath || []).map(String));
  const nodeIds = new Set();
  const edgeIds = new Set();
  let orderedNodeIds = [];

  if (activeValues.size) {
    grouped.nodes.forEach((node) => {
      if (allNodeAliases(node).some((alias) => activeValues.has(alias))) nodeIds.add(node.id);
    });
    visualEdges.forEach((edge) => {
      if (activeValues.has(edge.id) || activeValues.has(edge.rawEdgeId)) {
        edgeIds.add(edge.id);
        if (edge.rawEdgeId) edgeIds.add(edge.rawEdgeId);
        nodeIds.add(edge.source);
        nodeIds.add(edge.target);
      }
    });
    orderedNodeIds = (activePath || [])
      .map((id) => resolveNode(grouped.nodeById, id)?.id || String(id))
      .filter((id) => grouped.nodeById.has(id) || nodeIds.has(id));
    if (nodeIds.size && edgeIds.size === 0) {
      visualEdges.forEach((edge) => {
        if (!nodeIds.has(edge.source) && !nodeIds.has(edge.target)) return;
        edgeIds.add(edge.id);
        if (edge.rawEdgeId) edgeIds.add(edge.rawEdgeId);
        nodeIds.add(edge.source);
        nodeIds.add(edge.target);
      });
      orderedNodeIds = [...new Set([...orderedNodeIds, ...nodeIds])];
    }
    return { nodeIds, edgeIds, orderedNodeIds };
  }

  const { start, end } = inferEndpoints(grouped.nodes, visualEdges, startNodeId, endNodeId);
  const preferred = shortestPath(visualEdges, start, end, false, new Set(["FLOWS_TO"]));
  const directed = preferred || shortestPath(visualEdges, start, end, false);
  const path = directed || shortestPath(visualEdges, start, end, true);

  if (path) {
    path.nodes.forEach((id) => nodeIds.add(id));
    path.edgeIds.forEach((id) => edgeIds.add(id));
    orderedNodeIds = path.nodes;
  } else if (start) {
    nodeIds.add(start);
    orderedNodeIds = [start];
  }

  if (nodeIds.size && edgeIds.size === 0) {
    visualEdges.forEach((edge) => {
      if (!nodeIds.has(edge.source) && !nodeIds.has(edge.target)) return;
      edgeIds.add(edge.id);
      if (edge.rawEdgeId) edgeIds.add(edge.rawEdgeId);
      nodeIds.add(edge.source);
      nodeIds.add(edge.target);
    });
    orderedNodeIds = [...new Set([...orderedNodeIds, ...nodeIds])];
  }

  return { nodeIds, edgeIds, orderedNodeIds };
}

function collectCardEdges(cards, itemToCard, visualEdges) {
  const cardIds = new Set(cards.map((card) => card.id));
  const seen = new Set();
  const cardEdges = [];
  visualEdges.forEach((edge) => {
    const sourceCard = itemToCard.get(edge.source);
    const targetCard = itemToCard.get(edge.target);
    if (!sourceCard || !targetCard || sourceCard === targetCard) return;
    if (!cardIds.has(sourceCard) || !cardIds.has(targetCard)) return;
    const key = `${sourceCard}->${targetCard}:${edge.id}`;
    if (seen.has(key)) return;
    seen.add(key);
    cardEdges.push({ ...edge, sourceCard, targetCard });
  });
  return cardEdges;
}

export function buildLayeredLayout(cards, visualEdges, itemToCard, activePathState, expandedCards = {}, openCards = {}) {
  const cardEdges = collectCardEdges(cards, itemToCard, visualEdges);
  const activeCardOrder = [];
  (activePathState?.orderedNodeIds || []).forEach((nodeId) => {
    const cardId = itemToCard.get(nodeId);
    if (cardId && !activeCardOrder.includes(cardId)) activeCardOrder.push(cardId);
  });

  const layers = new Map();
  const inDegree = new Map(cards.map((card) => [card.id, 0]));
  cardEdges.forEach((edge) => inDegree.set(edge.targetCard, (inDegree.get(edge.targetCard) || 0) + 1));
  const roots = cards.filter((card) => (inDegree.get(card.id) || 0) === 0);
  const queue = roots.length ? roots.map((card) => card.id) : cards.slice(0, 1).map((card) => card.id);
  queue.forEach((cardId) => layers.set(cardId, 0));
  const seen = new Set(queue);
  while (queue.length) {
    const cardId = queue.shift();
    const sourceLayer = layers.get(cardId) || 0;
    cardEdges
      .filter((edge) => edge.sourceCard === cardId)
      .forEach((edge) => {
        if (!layers.has(edge.targetCard)) layers.set(edge.targetCard, sourceLayer + 1);
        else layers.set(edge.targetCard, Math.max(layers.get(edge.targetCard), sourceLayer + 1));
        if (!seen.has(edge.targetCard)) {
          seen.add(edge.targetCard);
          queue.push(edge.targetCard);
        }
      });
  }
  cards.forEach((card) => {
    if (!layers.has(card.id)) layers.set(card.id, Math.max(0, ...layers.values()) + 1);
  });

  const maxNonUsageLayer = Math.max(
    0,
    ...cards.filter((card) => card.kind !== "usage").map((card) => layers.get(card.id) || 0)
  );
  cards.forEach((card) => {
    if (card.kind === "source") layers.set(card.id, 0);
    if (card.kind === "usage") layers.set(card.id, Math.max(layers.get(card.id) || 0, maxNonUsageLayer + 1));
  });
  cardEdges.forEach((edge) => {
    const source = cards.find((card) => card.id === edge.sourceCard);
    const target = cards.find((card) => card.id === edge.targetCard);
    if (source?.kind === "usage" && target?.kind === "usage") {
      layers.set(target.id, Math.max(layers.get(target.id) || 0, (layers.get(source.id) || 0) + 1));
    }
  });

  const activeCards = new Set(activeCardOrder);
  const byLayer = new Map();
  cards.forEach((card) => {
    const layer = layers.get(card.id) || 0;
    if (!byLayer.has(layer)) byLayer.set(layer, []);
    byLayer.get(layer).push(card);
  });

  const positions = new Map();
  [...byLayer.entries()].forEach(([layer, layerCards]) => {
    let y = BOARD_PADDING + 34;
    layerCards
      .sort((left, right) => {
        const leftActive = activeCards.has(left.id) ? activeCardOrder.indexOf(left.id) : Number.MAX_SAFE_INTEGER;
        const rightActive = activeCards.has(right.id) ? activeCardOrder.indexOf(right.id) : Number.MAX_SAFE_INTEGER;
        return leftActive - rightActive || left.title.localeCompare(right.title);
      })
      .forEach((card) => {
        const cardOpen = openCards[card.id] !== false;
        const visibleRows = cardOpen ? visibleCardRows(card, activePathState?.nodeIds || new Set(), expandedCards[card.id]).rows : [];
        const hasFooter = cardOpen && card.items.length > DEFAULT_VISIBLE_ROWS;
        const height = HEADER_HEIGHT + visibleRows.length * ROW_HEIGHT + (hasFooter ? FOOTER_HEIGHT : 12);
        positions.set(card.id, {
          x: BOARD_PADDING + layer * (CARD_WIDTH + COLUMN_GAP),
          y,
          width: CARD_WIDTH,
          height,
          layer,
          visibleRows,
          open: cardOpen,
        });
        y += height + CARD_GAP + Math.min(18, Math.max(0, visibleRows.length - 4) * 2);
      });
  });

  const width = Math.max(720, ...[...positions.values()].map((position) => position.x + position.width + COLUMN_GAP));
  const height = Math.max(560, ...[...positions.values()].map((position) => position.y + position.height + BOARD_PADDING * 2));
  return { positions, cardEdges, width, height };
}

function visibleCardRows(card, activeNodeIds, expanded) {
  const rows = card.items || [];
  if (expanded || rows.length <= DEFAULT_VISIBLE_ROWS) return { rows, forced: false };
  const visible = rows.slice(0, DEFAULT_VISIBLE_ROWS);
  let forced = false;
  rows.slice(DEFAULT_VISIBLE_ROWS).forEach((row) => {
    if (activeNodeIds.has(row.id) && !visible.some((item) => item.id === row.id)) {
      forced = true;
      visible.splice(Math.max(0, DEFAULT_VISIBLE_ROWS - 1), 1, row);
    }
  });
  return { rows: visible, forced };
}

function endpointAnchor(position, endpoint, isSourceEndpoint) {
  if (!position) return null;
  const rows = position.visibleRows || [];
  const rowIndex = rows.findIndex((row) => row.id === endpoint);
  const y = rowIndex >= 0 ? position.y + HEADER_HEIGHT + rowIndex * ROW_HEIGHT + ROW_HEIGHT / 2 : position.y + position.height / 2;
  return {
    x: isSourceEndpoint ? position.x + position.width : position.x,
    y,
  };
}

function pathForEdge(edge, positions) {
  const sourcePosition = positions.get(edge.sourceCard);
  const targetPosition = positions.get(edge.targetCard);
  if (!sourcePosition || !targetPosition) return "";
  const from = endpointAnchor(sourcePosition, edge.source, true);
  const to = endpointAnchor(targetPosition, edge.target, false);
  if (!from || !to) return "";
  const mid = from.x + Math.max(40, (to.x - from.x) / 2);
  return `M ${from.x} ${from.y} C ${mid} ${from.y}, ${mid} ${to.y}, ${to.x} ${to.y}`;
}

function dqcTone(items = []) {
  if (!items.length) return null;
  if (
    items.some(
      (item) =>
        item.__unresolved ||
        item.control_status === "FAILED" ||
        String(item.status || "").toUpperCase() === "KO" ||
        String(item.status || "").toUpperCase() === "FAILED"
    )
  ) return "critical";
  if (items.some((item) => item.human_review_required || item.confidence_level === "MEDIUM" || item.control_status === "NO_THRESHOLD")) return "review";
  return "good";
}

function dqcLabel(items = []) {
  const tone = dqcTone(items);
  if (!tone) return "";
  if (tone === "critical") return "DQC issue";
  if (tone === "review") return "DQC review";
  return "DQC ok";
}

function collectDqc(card, getDqcItems) {
  if (!getDqcItems) return [];
  const seen = new Set();
  const items = [];
  card.rawNodes.forEach((node) => {
    (getDqcItems(node.raw) || []).forEach((item) => {
      const key = item.check_id || item.id || item.resolved_id || JSON.stringify(item);
      if (seen.has(key)) return;
      seen.add(key);
      items.push(item);
    });
  });
  return items;
}

function qualityTone(value) {
  const status = normalizeText(value?.status || value?.usage_quality_status || value?.source_quality_status || value);
  const score = Number(value?.score ?? value?.usage_quality_score ?? value?.source_quality_score);
  if (status.includes("critical") || status.includes("failed") || status.includes("ko") || status.includes("error")) return "critical";
  if (status.includes("warning") || status.includes("review") || status.includes("medium")) return "review";
  if (status.includes("ok") || status.includes("valid") || status.includes("passed") || status.includes("green")) return "good";
  if (Number.isFinite(score)) {
    if (score < 50) return "critical";
    if (score < 80) return "review";
    return "good";
  }
  return "neutral";
}

function usageQualityRows(node, dqcItems = []) {
  const props = rawProps(node?.raw);
  const quality = node?.quality || node?.raw?.quality || props.quality || {};
  const checks = [
    ...(node?.qualityChecks || []),
    ...(node?.raw?.quality_checks || []),
    ...(props.quality_checks || []),
    ...dqcItems,
  ];
  const seen = new Set();
  const uniqueChecks = checks.filter((item) => {
    const key = item?.check_id || item?.id || `${item?.control_source}:${item?.field}:${item?.control_name}:${item?.score}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return {
    quality,
    checks: uniqueChecks,
    usage: {
      label: "Niveau de qualité Usage",
      score: firstNonEmpty(quality.usage_quality_score, props.usage_quality_score),
      status: firstNonEmpty(quality.usage_quality_status, props.usage_quality_status, props.status),
    },
    source: {
      label: "Niveau de qualité des sources",
      score: firstNonEmpty(quality.source_quality_score, props.source_quality_score),
      status: firstNonEmpty(quality.source_quality_status, props.source_quality_status),
    },
  };
}

function dqcQualitySummary(items = [], label = "Niveau de qualite", fallbackQuality = null) {
  const tone = dqcTone(items);
  const scores = items
    .map((item) => Number(firstNonEmpty(item.score, item.quality_score, item.control_score)))
    .filter((score) => Number.isFinite(score));
  const fallbackScore = firstNonEmpty(fallbackQuality?.score, fallbackQuality?.source_quality_score, fallbackQuality?.field_quality_score);
  const fallbackStatus = firstNonEmpty(fallbackQuality?.status, fallbackQuality?.source_quality_status, fallbackQuality?.field_quality_status);
  const average = scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : fallbackScore;
  return {
    label,
    score: average,
    status: tone === "critical" ? "Critical" : tone === "review" ? "Warning" : tone === "good" ? "OK" : fallbackStatus || "N/A",
    tone: tone || qualityTone({ score: average, status: fallbackStatus }),
    checks: items,
  };
}

function formatScore(value) {
  if (value === undefined || value === null || value === "") return "-";
  const number = Number(value);
  if (Number.isFinite(number)) return `${Math.round(number * 10) / 10}%`;
  return String(value);
}

function LineageCard({
  card,
  position,
  activeNodeIds,
  dragging,
  expanded,
  open,
  rowExpansion,
  getDqcItems,
  showDqc,
  onToggleOpen,
  onToggleRows,
  onNodeClick,
  onItemClick,
  onItemExpand,
  onQualityClick,
  onStartDrag,
}) {
  const { rows, forced } = visibleCardRows(card, activeNodeIds, expanded);
  const activeCard = card.rawNodes.some((node) => activeNodeIds.has(node.id));
  const dqcItems = showDqc ? collectDqc(card, getDqcItems) : [];
  const canPageRows = open && card.items.length > DEFAULT_VISIBLE_ROWS;
  const hasHidden = open && card.items.length > rows.length;
  const dqcCardTone = dqcTone(dqcItems);
  const usageQuality = card.kind === "usage" ? usageQualityRows(card.node, dqcItems) : null;
  const sourceQuality =
    card.kind === "source" && showDqc && (dqcItems.length > 0 || card.node.quality)
      ? dqcQualitySummary(dqcItems, "Niveau de qualite Source", card.node.quality)
      : null;
  return (
    <article
      className={cls(
        "dgx-card",
        `kind-${card.kind}`,
        activeCard && "is-active",
        dragging && "dragging",
        dqcCardTone && `dqc-${dqcCardTone}`
      )}
      style={{ left: position.x, top: position.y, width: position.width, minHeight: position.height }}
      onMouseDown={(event) => {
        if (event.button !== 0) return;
        if (event.target.closest("button, .dgx-plus, .dgx-row-dqc")) return;
        onStartDrag?.(event, card.id, position);
      }}
      onClick={() => onNodeClick?.(card.node.raw, card)}
    >
      <header className="dgx-card-header">
        <button
          type="button"
          className="dgx-card-caret"
          title={open ? "Collapse card" : "Expand card"}
          onClick={(event) => {
            event.stopPropagation();
            onToggleOpen(card.id);
          }}
        >
          {open ? "-" : "+"}
        </button>
        <span className={cls("dgx-type-icon", `kind-${card.kind}`)}>{iconForKind(card.kind)}</span>
        <span className="dgx-card-title">
          <small title={card.path}>{card.path || card.kind}</small>
          <strong title={card.title}>{card.title}</strong>
        </span>
        {showDqc && dqcItems.length > 0 && (
          <span
            className={cls("dgx-dqc-voyant", dqcCardTone === "good" ? "passed" : "failed")}
            title={dqcCardTone === "good" ? "Data quality checks passed" : "Data quality checks failed or need review"}
          />
        )}
        <span className="dgx-card-badges">
          <span className="dgx-card-badge">{cardBadge(card.kind)}</span>
          {showDqc && dqcItems.length > 0 && (
            <span className={cls("dgx-dqc-badge", dqcCardTone)}>{dqcLabel(dqcItems)}</span>
          )}
        </span>
      </header>
      {usageQuality && (
        <div className="dgx-usage-quality">
          {[usageQuality.usage, usageQuality.source].map((item) => {
            const tone = qualityTone(item);
            return (
              <button
                key={item.label}
                type="button"
                className={cls("dgx-quality-tag", tone)}
                title="Show quality control details"
                onClick={(event) => {
                  event.stopPropagation();
                  onQualityClick?.(card, item.label, usageQuality.checks);
                }}
              >
                <span>{item.label}</span>
                <strong>{formatScore(item.score)}</strong>
                <em>{item.status || "N/A"}</em>
              </button>
            );
          })}
        </div>
      )}
      {sourceQuality && (
        <div className="dgx-usage-quality">
          <button
            type="button"
            className={cls("dgx-quality-tag", sourceQuality.tone)}
            title="Show source quality control details"
            onClick={(event) => {
              event.stopPropagation();
              onQualityClick?.(card, sourceQuality.label, sourceQuality.checks);
            }}
          >
            <span>{sourceQuality.label}</span>
            <strong>{formatScore(sourceQuality.score)}</strong>
            <em>{sourceQuality.status}</em>
          </button>
        </div>
      )}
      {open && <div className="dgx-card-body">
        {rows.map((item) => {
          const active = activeNodeIds.has(item.id);
          const itemDqc = showDqc ? getDqcItems?.(item.raw) || [] : [];
          const itemDqcTone = dqcTone(itemDqc);
          const itemExpanded = rowExpansion?.expanded?.(item.raw, item);
          const itemLoading = rowExpansion?.loading?.(item.raw, item);
          return (
            <button
              key={item.id}
              type="button"
              className={cls(
                "dgx-row",
                item.visualDepth > 0 && "nested",
                item.kind === "structure" && "structure-row",
                active && "is-active",
                itemDqcTone && `dqc-${itemDqcTone}`
              )}
              onClick={(event) => {
                event.stopPropagation();
                onItemClick?.(item.raw, item, card);
              }}
            >
              <span className="dgx-row-icon">{iconForKind(item.kind)}</span>
              <span className="dgx-row-name" title={item.displayName}>{item.displayName}</span>
              {item.kind === "data_processing_item" && active && <span className="dgx-row-badge">DPI</span>}
              {showDqc && itemDqc.length > 0 && (
                <span
                  className={cls("dgx-row-dqc", itemDqcTone, "clickable")}
                  role="button"
                  tabIndex={0}
                  title="Show DQC control details"
                  onClick={(event) => {
                    event.stopPropagation();
                    onQualityClick?.(card, `DQC controls - ${item.displayName}`, itemDqc);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      event.stopPropagation();
                      onQualityClick?.(card, `DQC controls - ${item.displayName}`, itemDqc);
                    }
                  }}
                >
                  DQC
                </span>
              )}
              <span className="dgx-status-dot" aria-hidden="true" />
              <span
                className={cls("dgx-plus", itemExpanded && "expanded")}
                role="button"
                tabIndex={0}
                title={itemExpanded ? "Collapse lineage links" : "Expand lineage links"}
                onClick={(event) => {
                  event.stopPropagation();
                  onItemExpand?.(item.raw, item, card);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    event.stopPropagation();
                    onItemExpand?.(item.raw, item, card);
                  }
                }}
              >
                {itemLoading ? "..." : itemExpanded ? "-" : "+"}
              </span>
            </button>
          );
        })}
      </div>}
      {canPageRows && (
        <footer className="dgx-card-footer">
          <span>{expanded ? `Affiche ${card.items.length} / ${card.items.length}` : `Affiche ${rows.length} / ${card.items.length}`}</span>
          {forced && <span>active visible</span>}
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onToggleRows(card.id);
            }}
          >
            {expanded ? "Collapse" : "Expand"}
          </button>
        </footer>
      )}
    </article>
  );
}

function QualityDetailsPanel({ panel, position, boardWidth, onClose }) {
  if (!panel || !position) return null;
  const rows = panel.checks || [];
  const panelWidth = 760;
  const maxLeft = Math.max(18, boardWidth - panelWidth - 18);
  const preferRight = position.x + position.width + 18 + panelWidth < boardWidth - 18;
  const wantedLeft = preferRight ? position.x + position.width + 18 : position.x - panelWidth - 18;
  const left = Math.max(18, Math.min(maxLeft, wantedLeft));
  return (
    <aside
      className="dgx-quality-panel"
      style={{ left, top: position.y + 18, width: panelWidth }}
      onClick={(event) => event.stopPropagation()}
    >
      <header>
        <span>
          <small>{panel.title}</small>
          <strong>{panel.cardTitle}</strong>
        </span>
        <button type="button" onClick={onClose} title="Close quality details">x</button>
      </header>
      <div className="dgx-quality-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Control</th>
              <th>Type</th>
              <th>Object</th>
              <th>Score</th>
              <th>Status</th>
              <th>Field</th>
              <th>Check</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? rows.map((item, index) => (
              <tr key={item.check_id || item.id || `${item.control_source}-${item.field}-${index}`}>
                <td>{item.control_source || "DQC"}</td>
                <td>
                  <strong>{item.control_name || item.quality_dimension || "Quality control"}</strong>
                  <small>{item.quality_dimension || item.control_tool || ""}</small>
                </td>
                <td>{item.controlled_object_type || item.matched_entity_level || "-"}</td>
                <td title={item.controlled_object_name || item.controlled_object_name_raw || item.matched_path_full || ""}>
                  {item.controlled_object_name || item.controlled_object_name_raw || item.controlled_structure_name || "-"}
                </td>
                <td>{formatScore(item.score ?? item.quality_score ?? item.control_score)}</td>
                <td><span className={cls("dgx-quality-status", qualityTone(item))}>{item.status || item.control_status || item.confidence_level || "-"}</span></td>
                <td>{item.field || item.controlled_field_name || "-"}</td>
                <td title={String(item.check_id || item.id || "")}>{compact(item.check_id || item.id || "-", 14)}</td>
              </tr>
            )) : (
              <tr>
                <td colSpan="8">No detailed controls are attached to this entity yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </aside>
  );
}

export default function LineageDatagalaxyView({
  nodes = [],
  edges = [],
  activePath,
  startNodeId,
  endNodeId,
  showDqc = false,
  getDqcItems,
  rowExpansion,
  issuesOnly = false,
  onNodeClick,
  onItemClick,
  onItemExpand,
}) {
  const [expandedCards, setExpandedCards] = useState({});
  const [openCards, setOpenCards] = useState({});
  const [manualPositions, setManualPositions] = useState(() => new Map());
  const [draggingCardId, setDraggingCardId] = useState(null);
  const [qualityPanel, setQualityPanel] = useState(null);
  const [zoom, setZoom] = useState(1);
  const shellRef = useRef(null);
  const footprintRef = useRef({ cards: 0, width: 0, height: 0 });
  const dragRef = useRef({ active: false, cardId: null, startX: 0, startY: 0, originX: 0, originY: 0 });
  const grouped = useMemo(() => groupNodesIntoCards(nodes, edges), [nodes, edges]);
  const visibleCards = useMemo(() => {
    if (!issuesOnly || !showDqc || !getDqcItems) return grouped.cards;
    return grouped.cards.filter((card) => collectDqc(card, getDqcItems).length > 0);
  }, [grouped.cards, issuesOnly, showDqc, getDqcItems]);
  const visualEdges = useMemo(() => deriveVisualLineageEdges(nodes, edges), [nodes, edges]);
  const activeState = useMemo(
    () => computeActivePath({ nodes, edges, activePath, startNodeId, endNodeId }),
    [nodes, edges, activePath, startNodeId, endNodeId]
  );
  const layout = useMemo(
    () => buildLayeredLayout(visibleCards, visualEdges, grouped.itemToCard, activeState, expandedCards, openCards),
    [visibleCards, grouped.itemToCard, visualEdges, activeState, expandedCards, openCards]
  );
  const draggableLayout = useMemo(() => {
    const positions = new Map();
    layout.positions.forEach((position, cardId) => {
      const manual = manualPositions.get(cardId);
      positions.set(cardId, manual ? { ...position, x: manual.x, y: manual.y } : position);
    });
    const width = Math.max(layout.width, ...[...positions.values()].map((position) => position.x + position.width + BOARD_PADDING));
    const height = Math.max(layout.height, ...[...positions.values()].map((position) => position.y + position.height + BOARD_PADDING));
    return { ...layout, positions, width, height };
  }, [layout, manualPositions]);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell || !visibleCards.length) return;

    const previous = footprintRef.current;
    const grew =
      visibleCards.length > previous.cards ||
      draggableLayout.width > previous.width + 80 ||
      draggableLayout.height > previous.height + 80;
    const shrank =
      visibleCards.length < previous.cards ||
      draggableLayout.width < previous.width - 120 ||
      draggableLayout.height < previous.height - 120;

    const fitX = (shell.clientWidth - 36) / Math.max(draggableLayout.width, 1);
    const fitY = (shell.clientHeight - 36) / Math.max(draggableLayout.height, 1);
    const fitZoom = Math.max(MIN_AUTO_ZOOM, Math.min(MAX_AUTO_ZOOM, fitX, fitY));

    if (grew) {
      setZoom((current) => Math.max(MIN_AUTO_ZOOM, Math.max(fitZoom, current - 0.08)));
    } else if (shrank) {
      setZoom((current) => Math.min(MAX_AUTO_ZOOM, Math.max(fitZoom, current + 0.05)));
    }

    footprintRef.current = {
      cards: visibleCards.length,
      width: draggableLayout.width,
      height: draggableLayout.height,
    };
  }, [visibleCards.length, draggableLayout.width, draggableLayout.height]);

  useEffect(() => {
    function handleMouseMove(event) {
      if (!dragRef.current.active) return;
      event.preventDefault();
      const dx = event.clientX - dragRef.current.startX;
      const dy = event.clientY - dragRef.current.startY;
      setManualPositions((current) => {
        const next = new Map(current);
        next.set(dragRef.current.cardId, {
          x: Math.max(BOARD_PADDING / 2, dragRef.current.originX + dx / Math.max(zoom, 0.1)),
          y: Math.max(BOARD_PADDING / 2, dragRef.current.originY + dy / Math.max(zoom, 0.1)),
        });
        return next;
      });
    }

    function handleMouseUp() {
      if (!dragRef.current.active) return;
      dragRef.current.active = false;
      setDraggingCardId(null);
    }

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [zoom]);

  function toggleCard(cardId) {
    setExpandedCards((current) => ({ ...current, [cardId]: !current[cardId] }));
  }

  function toggleCardOpen(cardId) {
    setOpenCards((current) => ({ ...current, [cardId]: current[cardId] === false ? true : false }));
  }

  function startCardDrag(event, cardId, position) {
    event.preventDefault();
    event.stopPropagation();
    dragRef.current = {
      active: true,
      cardId,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
    };
    setDraggingCardId(cardId);
  }

  function showQualityPanel(card, title, checks) {
    setQualityPanel((current) => {
      const key = `${card.id}:${title}`;
      if (current?.key === key) return null;
      return {
        key,
        cardId: card.id,
        cardTitle: card.title,
        title,
        checks,
      };
    });
  }

  if (!nodes.length) {
    return (
      <div className="dgx-empty">
        <strong>No lineage data</strong>
        <span>Load a lineage path or the demo dataset to render DataGalaxy view.</span>
      </div>
    );
  }

  return (
    <div className="dgx-shell" ref={shellRef}>
      <div className="dgx-zoom-chip">{Math.round(zoom * 100)}%</div>
      <div
        className="dgx-board"
        style={{
          width: draggableLayout.width,
          height: draggableLayout.height,
          transform: `scale(${zoom})`,
        }}
      >
        <div className="dgx-direction-banner">
          <strong>Golden sources</strong>
          <span />
          <strong>Usage</strong>
        </div>
        <svg className="dgx-edges" width={draggableLayout.width} height={draggableLayout.height}>
          <defs>
            <marker id="dgx-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
              <path d="M0,0 L9,4.5 L0,9 Z" />
            </marker>
            <marker id="dgx-arrow-active" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto">
              <path d="M0,0 L9,4.5 L0,9 Z" />
            </marker>
          </defs>
          {layout.cardEdges.map((edge) => {
            const active =
              activeState.edgeIds.has(edge.id) ||
              activeState.edgeIds.has(edge.rawEdgeId) ||
              (activeState.nodeIds.has(edge.source) && activeState.nodeIds.has(edge.target)) ||
              activeState.nodeIds.has(edge.source) ||
              activeState.nodeIds.has(edge.target);
            return (
              <path
                key={edge.id}
                className={cls("dgx-edge", active && "is-active")}
                d={pathForEdge(edge, draggableLayout.positions)}
              />
            );
          })}
        </svg>
        {visibleCards.map((card) => {
          const position = draggableLayout.positions.get(card.id);
          if (!position) return null;
          return (
            <LineageCard
              key={card.id}
              card={card}
              position={position}
              activeNodeIds={activeState.nodeIds}
              dragging={draggingCardId === card.id}
              expanded={expandedCards[card.id]}
              open={openCards[card.id] !== false}
              rowExpansion={rowExpansion}
              showDqc={showDqc}
              getDqcItems={getDqcItems}
              onToggleOpen={toggleCardOpen}
              onToggleRows={toggleCard}
              onNodeClick={onNodeClick}
              onItemClick={onItemClick}
              onItemExpand={onItemExpand}
              onQualityClick={showQualityPanel}
              onStartDrag={startCardDrag}
            />
          );
        })}
        <QualityDetailsPanel
          panel={qualityPanel}
          position={qualityPanel ? draggableLayout.positions.get(qualityPanel.cardId) : null}
          boardWidth={draggableLayout.width}
          onClose={() => setQualityPanel(null)}
        />
      </div>
    </div>
  );
}
