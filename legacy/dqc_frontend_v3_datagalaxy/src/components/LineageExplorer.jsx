import React, { useEffect, useMemo, useRef, useState } from "react";

import LineageDatagalaxyView, {
  computeFocusedLineagePath,
  deriveVisualLineageEdges,
  groupNodesIntoCards,
} from "./LineageDatagalaxyView";
import {
  askDqcAgent,
  fetchBusinessLineage,
  fetchResolvedDqc,
  fetchUnresolvedDqc,
  searchAssets,
} from "../api";
import { mockLineageGraph } from "../mockLineageData";
import {
  assetIcon,
  classifyAssetType,
  getNodeName,
  getNodePath,
  normalizeText,
} from "../lineageUtils";

const LEVEL_WIDTH = 340;
const CARD_WIDTH = 292;
const CARD_Y_GAP = 34;
const BOARD_TOP = 82;
const BOARD_LEFT = 48;
const CARD_COLLISION_GAP = 18;
const LINEAGE_EDGE_TYPES = new Set(["IS_INPUT_OF", "IS_OUTPUT_OF"]);

function cls(...items) {
  return items.filter(Boolean).join(" ");
}

function safeItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.data?.items)) return payload.data.items;
  return [];
}

function mergeGraphs(current, incoming) {
  if (!current) {
    return {
      root: incoming?.root,
      nodes: Array.isArray(incoming?.nodes) ? incoming.nodes : [],
      edges: Array.isArray(incoming?.edges) ? incoming.edges : [],
    };
  }

  const nodes = new Map((current.nodes || []).map((node) => [node.id, node]));
  (incoming?.nodes || []).forEach((node) => nodes.set(node.id, { ...nodes.get(node.id), ...node }));

  const edges = new Map();
  (current.edges || []).forEach((edge) => edges.set(edge.id || `${edge.source}->${edge.target}:${edge.type}`, edge));
  (incoming?.edges || []).forEach((edge) => edges.set(edge.id || `${edge.source}->${edge.target}:${edge.type}`, edge));

  return {
    root: current.root || incoming?.root,
    nodes: [...nodes.values()],
    edges: [...edges.values()],
  };
}

function compact(value, max = 38) {
  const text = String(value || "");
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

function nodeKeys(node) {
  const props = node?.properties || {};
  return [
    node?.id,
    node?.node_id,
    props.node_id,
    props.path_full,
    props.path,
    props.technical_path,
    props.app_code,
    props.application_code,
    getNodeName(node),
    getNodePath(node),
  ]
    .filter(Boolean)
    .map((item) => normalizeText(item));
}

function dqcKeys(item) {
  return [
    item?.matched_node_id,
    item?.matched_path_full,
    item?.application_code_norm,
    item?.controlled_structure_name,
    item?.controlled_field_name,
    [item?.application_code_norm, item?.controlled_structure_name].filter(Boolean).join("."),
    [item?.application_code_norm, item?.controlled_structure_name, item?.controlled_field_name]
      .filter(Boolean)
      .join("."),
  ]
    .filter(Boolean)
    .map((value) => normalizeText(value));
}

function koRate(item) {
  const ko = Number(item?.ko_count ?? item?.kocount ?? 0);
  const total = Number(item?.controlled_item_count ?? item?.controlleditemcount ?? 0);
  if (!Number.isFinite(total) || total <= 0) return Number(item?.ko_rate ?? 0);
  return ko / total;
}

function badgeFromDqc(items = []) {
  if (!items.length) return null;
  if (items.some((item) => item.__unresolved)) return { tone: "critical", label: "Warning" };
  if (items.some((item) => item.human_review_required || item.confidence_level === "MEDIUM")) {
    return { tone: "review", label: "Needs review" };
  }
  if (items.some((item) => item.control_status === "FAILED" || String(item.status || "").toUpperCase() === "FAILED" || String(item.status || "").toUpperCase() === "KO")) {
    return { tone: "critical", label: "Issue" };
  }
  if (items.some((item) => item.confidence_level === "HIGH" || item.control_status === "PASSED" || String(item.status || "").toUpperCase() === "PASSED")) return { tone: "good", label: "Validated" };
  return { tone: "neutral", label: "DQC" };
}

function edgeType(edge) {
  return String(edge?.type || edge?.properties?.link_type || "").toUpperCase();
}

function canonicalNodeId(nodeOrId) {
  if (!nodeOrId) return "";
  if (typeof nodeOrId === "string") return nodeOrId;
  return String(nodeOrId.id || nodeOrId.node_id || nodeOrId.properties?.node_id || "");
}

function searchResultNodeId(result) {
  const direct = result?.node_id || result?.nodeId;
  if (direct) return direct;
  const id = String(result?.id || "");
  return id.replace(/^(source|container|structure|field|usage|dataset|process):/i, "");
}

function isDataProcessing(node) {
  const type = normalizeText(node?.type).replace(/\s+/g, "");
  return type === "dataprocessing";
}

function isDataProcessingItem(node) {
  return normalizeText(node?.type).replace(/\s+/g, "") === "dataprocessingitem";
}

function isSameNode(left, rightId) {
  if (!left || !rightId) return false;
  const id = String(rightId);
  return [left.id, left.node_id, left.properties?.node_id].filter(Boolean).some((value) => String(value) === id);
}

function isTrackedNode(node, trackedNodeIds) {
  if (!node || !trackedNodeIds?.size) return false;
  return [node.id, node.node_id, node.properties?.node_id]
    .filter(Boolean)
    .some((id) => trackedNodeIds.has(String(id)));
}

function lineageExpansionKey(nodeId, direction) {
  return `${nodeId}:${direction}`;
}

function hasLineageExpansion(expandedNodeIds, nodeId, direction) {
  if (!nodeId) return false;
  return (
    expandedNodeIds.has(String(nodeId)) ||
    expandedNodeIds.has(lineageExpansionKey(nodeId, direction)) ||
    expandedNodeIds.has(lineageExpansionKey(nodeId, "both"))
  );
}

function canUseKeyboardClick(event) {
  return event.key === "Enter" || event.key === " ";
}

function cardNodeIds(card) {
  const ids = new Set();
  [card.node, ...(card.structures || []), ...(card.processItems || [])].forEach((node) => {
    const id = canonicalNodeId(node);
    if (id) ids.add(id);
  });
  (card.structures || []).forEach((structure) => {
    (card.fieldsByStructure.get(structure.id) || []).forEach((field) => {
      const id = canonicalNodeId(field);
      if (id) ids.add(id);
    });
  });
  return ids;
}

function cardContainsNode(card, nodeId) {
  return cardNodeIds(card).has(String(nodeId || ""));
}

function inferSourceForStructure(structure, sources) {
  const path = normalizeText(getNodePath(structure));
  const name = normalizeText(getNodeName(structure));
  return sources.find((source) => {
    const sourceName = normalizeText(getNodeName(source));
    const sourcePath = normalizeText(getNodePath(source));
    return (
      (sourceName && (path.includes(sourceName) || name.includes(sourceName))) ||
      (sourcePath && path.includes(sourcePath))
    );
  });
}

function inferProcessForItem(item, processes) {
  const path = normalizeText(getNodePath(item));
  if (!path) return null;

  return processes.find((process) => {
    const processName = normalizeText(getNodeName(process));
    const processPath = normalizeText(getNodePath(process));
    return (
      (processName && path.includes(processName)) ||
      (processPath && (path.includes(processPath) || processPath.includes(path)))
    );
  });
}

function buildCards(graph, qualityByKey) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const sources = nodes.filter((node) => classifyAssetType(node.type) === "source");
  const structures = nodes.filter((node) => classifyAssetType(node.type) === "structure");
  const fields = nodes.filter((node) => classifyAssetType(node.type) === "field");
  const dataProcessings = nodes.filter(isDataProcessing);
  const dataProcessingItems = nodes.filter(isDataProcessingItem);
  const structureParent = new Map();
  const fieldParent = new Map();
  const processItemParent = new Map();

  edges.forEach((edge) => {
    const source = nodeMap.get(edge.source);
    const target = nodeMap.get(edge.target);
    if (!source || !target) return;
    const sourceType = classifyAssetType(source.type);
    const targetType = classifyAssetType(target.type);
    const type = edgeType(edge);
    if (sourceType === "source" && targetType === "structure") structureParent.set(target.id, source.id);
    if (targetType === "source" && sourceType === "structure") structureParent.set(source.id, target.id);
    if (sourceType === "structure" && targetType === "field") fieldParent.set(target.id, source.id);
    if (targetType === "structure" && sourceType === "field") fieldParent.set(source.id, target.id);
    if (type === "PART_OF" && isDataProcessingItem(source) && isDataProcessing(target)) {
      processItemParent.set(source.id, target.id);
    }
    if (type === "PART_OF" && isDataProcessingItem(target) && isDataProcessing(source)) {
      processItemParent.set(target.id, source.id);
    }
  });

  structures.forEach((structure) => {
    if (structureParent.has(structure.id)) return;
    const inferred = inferSourceForStructure(structure, sources);
    if (inferred) structureParent.set(structure.id, inferred.id);
  });

  dataProcessingItems.forEach((item) => {
    if (processItemParent.has(item.id)) return;
    const inferred = inferProcessForItem(item, dataProcessings);
    if (inferred) processItemParent.set(item.id, inferred.id);
  });

  const childrenBySource = new Map();
  const fieldsByStructure = new Map();
  const itemsByProcess = new Map();
  structures.forEach((structure) => {
    const sourceId = structureParent.get(structure.id);
    if (!sourceId) return;
    if (!childrenBySource.has(sourceId)) childrenBySource.set(sourceId, []);
    childrenBySource.get(sourceId).push(structure);
  });
  fields.forEach((field) => {
    const structureId = fieldParent.get(field.id);
    if (!structureId) return;
    if (!fieldsByStructure.has(structureId)) fieldsByStructure.set(structureId, []);
    fieldsByStructure.get(structureId).push(field);
  });
  dataProcessingItems.forEach((item) => {
    const processId = processItemParent.get(item.id);
    if (!processId) return;
    if (!itemsByProcess.has(processId)) itemsByProcess.set(processId, []);
    itemsByProcess.get(processId).push(item);
  });

  const nodeToCard = new Map();
  const cards = [];
  sources.forEach((source) => {
    const card = {
      id: `source:${source.id}`,
      node: source,
      kind: "source",
      structures: childrenBySource.get(source.id) || [],
      fieldsByStructure,
      processItems: [],
    };
    cards.push(card);
    nodeToCard.set(source.id, card.id);
    card.structures.forEach((structure) => {
      nodeToCard.set(structure.id, card.id);
      (fieldsByStructure.get(structure.id) || []).forEach((field) => nodeToCard.set(field.id, card.id));
    });
  });

  dataProcessings.forEach((process) => {
    if (nodeToCard.has(process.id)) return;
    const card = {
      id: `process:${process.id}`,
      node: process,
      kind: "process",
      structures: [],
      fieldsByStructure,
      processItems: itemsByProcess.get(process.id) || [],
    };
    cards.push(card);
    nodeToCard.set(process.id, card.id);
    card.processItems.forEach((item) => nodeToCard.set(item.id, card.id));
  });

  nodes.forEach((node) => {
    if (nodeToCard.has(node.id)) return;
    if (isDataProcessingItem(node) && processItemParent.has(node.id)) return;
    const isStandaloneStructure = classifyAssetType(node.type) === "structure";
    const card = {
      id: `node:${node.id}`,
      node,
      kind: classifyAssetType(node.type),
      structures: isStandaloneStructure ? [node] : [],
      fieldsByStructure,
      processItems: [],
    };
    cards.push(card);
    nodeToCard.set(node.id, card.id);
  });

  const cardsById = new Map(cards.map((card) => [card.id, card]));
  cards.forEach((card) => {
    const quality = collectQualityForCard(card, qualityByKey);
    card.qualityItems = quality;
    card.badge = badgeFromDqc(quality);
  });

  const links = [];
  const seen = new Set();
  edges.forEach((edge, index) => {
    const sourceCard = nodeToCard.get(edge.source);
    const targetCard = nodeToCard.get(edge.target);
    if (!sourceCard || !targetCard || sourceCard === targetCard) return;
    const key = `${sourceCard}:${edge.source}->${targetCard}:${edge.target}:${edgeType(edge)}`;
    if (seen.has(key)) return;
    seen.add(key);
    const sourceQuality = cardsById.get(sourceCard)?.badge;
    const targetQuality = cardsById.get(targetCard)?.badge;
    links.push({
      id: `${key}-${index}`,
      source: sourceCard,
      target: targetCard,
      sourceNode: edge.source,
      targetNode: edge.target,
      type: edge.type || "lineage",
      relation: edgeType(edge),
      warning: sourceQuality?.tone === "critical" || targetQuality?.tone === "critical",
    });
  });

  return { cards, links, nodeMap, nodeToCard };
}

function collectQualityForCard(card, qualityByKey) {
  const nodes = [card.node, ...card.structures, ...(card.processItems || [])];
  card.structures.forEach((structure) => {
    nodes.push(...(card.fieldsByStructure.get(structure.id) || []));
  });
  const items = [];
  const seen = new Set();
  nodes.forEach((node) => {
    nodeKeys(node).forEach((key) => {
      (qualityByKey.get(key) || []).forEach((item) => {
        const id = item.id || JSON.stringify(item);
        if (!seen.has(id)) {
          seen.add(id);
          items.push(item);
        }
      });
    });
  });
  return items;
}

function collectQualityForNode(node, qualityByKey) {
  const items = [];
  const seen = new Set();
  const nodeChecks = [
    ...(node?.quality_checks || []),
    ...(node?.properties?.quality_checks || []),
  ];
  nodeChecks.forEach((item) => {
    const id = item.check_id || item.id || JSON.stringify(item);
    if (!seen.has(id)) {
      seen.add(id);
      items.push(item);
    }
  });
  nodeKeys(node).forEach((key) => {
    (qualityByKey.get(key) || []).forEach((item) => {
      const id = item.check_id || item.id || JSON.stringify(item);
      if (!seen.has(id)) {
        seen.add(id);
        items.push(item);
      }
    });
  });
  return items;
}

function getExpandedCardHeight(card, expanded) {
  if (!expanded?.structures && !expanded?.items) return 104;

  const openFields = expanded?.fieldIds?.size
    ? card.structures.reduce(
        (sum, structure) =>
          sum + (expanded.fieldIds.has(structure.id) ? card.fieldsByStructure.get(structure.id)?.length || 0 : 0),
        0
      )
    : 0;

  const structureRows = expanded?.structures ? Math.max(card.structures.length, 1) : 0;
  const itemRows = expanded?.items ? Math.max(card.processItems?.length || 0, 1) : 0;
  return 116 + structureRows * 44 + openFields * 34 + itemRows * 36;
}

function resolveVerticalSpace(levels, positions) {
  levels.forEach((level) => {
    const ordered = [...level.cards].sort((left, right) => {
      const leftPosition = positions.get(left.id);
      const rightPosition = positions.get(right.id);
      return (leftPosition?.y || 0) - (rightPosition?.y || 0);
    });

    let nextFreeY = BOARD_TOP;
    ordered.forEach((card) => {
      const position = positions.get(card.id);
      if (!position) return;
      const y = Math.max(position.y, nextFreeY);
      positions.set(card.id, { ...position, y });
      nextFreeY = y + position.height + CARD_COLLISION_GAP;
    });
  });
}

function buildVisibleHierarchy(cards, links, rootId, expandedNodeIds) {
  if (!rootId) {
    return { visibleCards: cards, visibleLinks: links, cardLevels: new Map(cards.map((card, index) => [card.id, index])) };
  }

  const roots = cards.filter((card) => cardContainsNode(card, rootId));
  const visibleCards = new Map(roots.map((card) => [card.id, card]));
  const cardLevels = new Map(roots.map((card) => [card.id, 0]));
  const visibleLinks = [];
  const queue = [...roots];
  const seenLinkIds = new Set();
  const cardsById = new Map(cards.map((card) => [card.id, card]));

  while (queue.length) {
    const current = queue.shift();
    const currentLevel = cardLevels.get(current.id) || 0;

    links.forEach((link) => {
      const forward = link.source === current.id && hasLineageExpansion(expandedNodeIds, link.sourceNode, "downstream");
      const backward = link.target === current.id && hasLineageExpansion(expandedNodeIds, link.targetNode, "upstream");
      if (!forward && !backward) return;
      if (seenLinkIds.has(link.id)) return;

      const nextCardId = forward ? link.target : link.source;
      const nextCard = cardsById.get(nextCardId);
      if (!nextCard) return;

      seenLinkIds.add(link.id);
      visibleLinks.push(link);

      const nextLevel = currentLevel + (forward ? 1 : -1);
      const existingLevel = cardLevels.get(nextCard.id);
      if (existingLevel === undefined || nextLevel < existingLevel) {
        cardLevels.set(nextCard.id, nextLevel);
      }

      if (!visibleCards.has(nextCard.id)) {
        visibleCards.set(nextCard.id, nextCard);
        queue.push(nextCard);
      }
    });
  }

  return {
    visibleCards: [...visibleCards.values()],
    visibleLinks,
    cardLevels,
  };
}

function enrichLayout(cards, links, expandedCards, expandedNodeIds, rootId, filters, manualPositions) {
  const hierarchy = buildVisibleHierarchy(cards, links, rootId, expandedNodeIds);
  const filtered = cards.filter((card) => {
    if (!hierarchy.visibleCards.some((visibleCard) => visibleCard.id === card.id)) return false;
    const text = normalizeText(`${getNodeName(card.node)} ${card.node?.node_id} ${getNodePath(card.node)}`);
    const matchSearch = !filters.canvasSearch || text.includes(normalizeText(filters.canvasSearch));
    const matchType = filters.assetType === "all" || card.kind === filters.assetType;
    const matchIssue = !filters.issuesOnly || ["critical", "review"].includes(card.badge?.tone);
    return matchSearch && matchType && matchIssue;
  });
  const visibleIds = new Set(filtered.map((card) => card.id));
  const rawLevels = filtered.map((card) => hierarchy.cardLevels.get(card.id) || 0);
  const minLevel = Math.min(0, ...rawLevels);
  const normalizedLevel = (card) => (hierarchy.cardLevels.get(card.id) || 0) - minLevel;
  const maxLevel = Math.max(0, ...filtered.map(normalizedLevel));
  const levels = Array.from({ length: maxLevel + 1 }, (_, level) => ({
    label: `Level ${level + 1}`,
    level,
    cards: filtered.filter((card) => normalizedLevel(card) === level),
  }));
  const positions = new Map();
  levels.forEach((level) => {
    let y = BOARD_TOP;
    level.cards.forEach((card) => {
      const expanded = expandedCards[card.id];
      const height = getExpandedCardHeight(card, expanded);
      const manual = manualPositions.get(card.id);
      positions.set(card.id, {
        x: manual?.x ?? BOARD_LEFT + level.level * LEVEL_WIDTH,
        y: manual?.y ?? y,
        height,
      });
      y += height + CARD_Y_GAP;
    });
  });
  resolveVerticalSpace(levels, positions);

  const visibleLinks = hierarchy.visibleLinks.filter((link) => visibleIds.has(link.source) && visibleIds.has(link.target));
  const boardHeight = Math.max(
    520,
    ...[...positions.values()].map((position) => position.y + position.height + 120)
  );
  const boardWidth = Math.max(
    BOARD_LEFT * 2 + LEVEL_WIDTH * Math.max(levels.length, 1),
    ...[...positions.values()].map((position) => position.x + CARD_WIDTH + 160)
  );
  return { columns: levels, positions, links: visibleLinks, boardHeight, boardWidth };
}

function QualityBadge({ badge, show = true }) {
  if (!badge || !show) return null;
  const warning = badge.tone === "critical" || badge.tone === "review";
  return (
    <span className={cls("dg-quality-badge", badge.tone)} title={warning ? "Data quality warning" : badge.label}>
      {badge.label}
    </span>
  );
}

function DirectionToggle({ direction, expanded, loading, onClick }) {
  return (
    <button
      type="button"
      className={cls("dg-direction-toggle", direction, expanded && "active")}
      title={direction === "upstream" ? "Expand upstream inputs" : "Expand downstream outputs"}
      onClick={onClick}
    >
      {loading ? "..." : expanded ? "-" : "+"}
    </button>
  );
}

function SourceCard({
  card,
  expanded,
  selectedId,
  selectedNodeId,
  showBadges,
  trackedNodeIds,
  onSelect,
  onToggleSource,
  onToggleStructure,
  onToggleLineageNode,
  onToggleLineageDirection,
  isDirectionExpanded,
  isNodeExpanded,
  isNodeLoading,
}) {
  const tracked =
    isTrackedNode(card.node, trackedNodeIds) ||
    card.structures.some(
      (structure) =>
        isTrackedNode(structure, trackedNodeIds) ||
        (card.fieldsByStructure.get(structure.id) || []).some((field) => isTrackedNode(field, trackedNodeIds))
    );

  return (
    <div
      className={cls("dg-card", card.kind, tracked && "tracked", selectedId === card.id && "selected", card.badge?.tone)}
      role="button"
      tabIndex={0}
      onClick={() => {
        onSelect({ type: "card", card, node: card.node });
      }}
      onKeyDown={(event) => {
        if (canUseKeyboardClick(event)) onSelect({ type: "card", card, node: card.node });
      }}
    >
      <DirectionToggle
        direction="upstream"
        expanded={isDirectionExpanded(card.node, "upstream")}
        loading={isNodeLoading(card.node)}
        onClick={(event) => {
          event.stopPropagation();
          onSelect({ type: "card", card, node: card.node });
          onToggleLineageDirection(card.node, "upstream");
        }}
      />
      <DirectionToggle
        direction="downstream"
        expanded={isDirectionExpanded(card.node, "downstream")}
        loading={isNodeLoading(card.node)}
        onClick={(event) => {
          event.stopPropagation();
          onSelect({ type: "card", card, node: card.node });
          onToggleLineageDirection(card.node, "downstream");
        }}
      />
      <CardHeader
        card={card}
        showBadges={showBadges}
        expandable
        expanded={isNodeExpanded(card.node)}
        onToggle={() => {
          onToggleSource(card.id);
          onToggleLineageNode(card.node);
        }}
      />
      {expanded?.structures && (
        <div className="dg-card-children">
          {card.structures.length === 0 && <div className="dg-empty-inline">No structures returned</div>}
          {card.structures.map((structure) => {
            const structureOpen = expanded.fieldIds?.has(structure.id);
            const structureTracked =
              isTrackedNode(structure, trackedNodeIds) ||
              (card.fieldsByStructure.get(structure.id) || []).some((field) => isTrackedNode(field, trackedNodeIds));
            return (
              <div key={structure.id} className="dg-structure-block">
                <div className="dg-row-with-direction">
                  <DirectionToggle
                    direction="upstream"
                    expanded={isDirectionExpanded(structure, "upstream")}
                    loading={isNodeLoading(structure)}
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelect({ type: "structure", card, node: structure });
                      onToggleLineageDirection(structure, "upstream");
                    }}
                  />
                  <button
                    type="button"
                    className={cls(
                      "dg-child-row",
                      structureOpen && "open",
                      structureTracked && "tracked",
                      isSameNode(structure, selectedNodeId) && "active"
                    )}
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelect({ type: "structure", card, node: structure });
                      onToggleStructure(card.id, structure.id);
                    }}
                  >
                    <span className="dg-row-icon">TBL</span>
                    <span>{compact(getNodeName(structure), 26)}</span>
                    <small>{structureOpen ? "-" : "+"}</small>
                  </button>
                  <DirectionToggle
                    direction="downstream"
                    expanded={isDirectionExpanded(structure, "downstream")}
                    loading={isNodeLoading(structure)}
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelect({ type: "structure", card, node: structure });
                      onToggleLineageDirection(structure, "downstream");
                    }}
                  />
                </div>
                {structureOpen && (
                  <div className="dg-field-list">
                    {(card.fieldsByStructure.get(structure.id) || []).map((field) => (
                      <div key={field.id} className="dg-row-with-direction">
                        <DirectionToggle
                          direction="upstream"
                          expanded={isDirectionExpanded(field, "upstream")}
                          loading={isNodeLoading(field)}
                          onClick={(event) => {
                            event.stopPropagation();
                            onSelect({ type: "field", card, node: field });
                            onToggleLineageDirection(field, "upstream");
                          }}
                        />
                        <button
                          type="button"
                          className={cls(
                            "dg-field-row",
                            isTrackedNode(field, trackedNodeIds) && "tracked",
                            isSameNode(field, selectedNodeId) && "active"
                          )}
                          onClick={(event) => {
                            event.stopPropagation();
                            onSelect({ type: "field", card, node: field });
                          }}
                        >
                          <span>FLD</span>
                          {compact(getNodeName(field), 28)}
                        </button>
                        <DirectionToggle
                          direction="downstream"
                          expanded={isDirectionExpanded(field, "downstream")}
                          loading={isNodeLoading(field)}
                          onClick={(event) => {
                            event.stopPropagation();
                            onSelect({ type: "field", card, node: field });
                            onToggleLineageDirection(field, "downstream");
                          }}
                        />
                      </div>
                    ))}
                    {(card.fieldsByStructure.get(structure.id) || []).length === 0 && (
                      <div className="dg-empty-inline">No fields returned</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CardHeader({ card, showBadges, expandable = false, expanded = false, onToggle }) {
  return (
    <div className="dg-card-head">
      <span className={cls("dg-asset-icon", card.kind)}>{assetIcon(card.node?.type)}</span>
      <span className="dg-card-copy">
        <small>{compact(getNodePath(card.node), 36) || card.kind}</small>
        <strong>{compact(getNodeName(card.node), 28)}</strong>
        <em>{card.node?.node_id || card.node?.id}</em>
      </span>
      <QualityBadge badge={card.badge} show={showBadges} />
      {expandable && (
        <button
          type="button"
          className="dg-expand-button"
          title={expanded ? "Collapse lineage items" : "Expand lineage items"}
          aria-label={expanded ? "Collapse lineage items" : "Expand lineage items"}
          onClick={(event) => {
            event.stopPropagation();
            onToggle?.();
          }}
        >
          {expanded ? "-" : "+"}
        </button>
      )}
    </div>
  );
}

function AssetCard({
  card,
  expanded,
  selectedId,
  selectedNodeId,
  showBadges,
  trackedNodeIds,
  onSelect,
  onToggleCard,
  onToggleLineageNode,
  onToggleLineageDirection,
  isDirectionExpanded,
  isNodeExpanded,
  isNodeLoading,
}) {
  const hasItems = (card.processItems || []).length > 0;
  const tracked =
    isTrackedNode(card.node, trackedNodeIds) ||
    (card.processItems || []).some((item) => isTrackedNode(item, trackedNodeIds));

  return (
    <div
      className={cls(
        "dg-card",
        card.kind,
        tracked && "tracked",
        selectedId === card.id && "selected",
        card.badge?.tone
      )}
      role="button"
      tabIndex={0}
      onClick={() => onSelect({ type: "card", card, node: card.node })}
      onKeyDown={(event) => {
        if (canUseKeyboardClick(event)) onSelect({ type: "card", card, node: card.node });
      }}
    >
      <DirectionToggle
        direction="upstream"
        expanded={isDirectionExpanded(card.node, "upstream")}
        loading={isNodeLoading(card.node)}
        onClick={(event) => {
          event.stopPropagation();
          onSelect({ type: "card", card, node: card.node });
          onToggleLineageDirection(card.node, "upstream");
        }}
      />
      <DirectionToggle
        direction="downstream"
        expanded={isDirectionExpanded(card.node, "downstream")}
        loading={isNodeLoading(card.node)}
        onClick={(event) => {
          event.stopPropagation();
          onSelect({ type: "card", card, node: card.node });
          onToggleLineageDirection(card.node, "downstream");
        }}
      />
      <CardHeader
        card={card}
        showBadges={showBadges}
        expandable
        expanded={isNodeExpanded(card.node)}
        onToggle={() => {
          if (hasItems) onToggleCard(card.id);
          onToggleLineageNode(card.node);
        }}
      />
      {expanded?.items && (
        <div className="dg-process-items">
          {hasItems ? (
            card.processItems.map((item) => (
              <div key={item.id} className="dg-row-with-direction">
                <DirectionToggle
                  direction="upstream"
                  expanded={isDirectionExpanded(item, "upstream")}
                  loading={isNodeLoading(item)}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect({ type: "data-processing-item", card, node: item });
                    onToggleLineageDirection(item, "upstream");
                  }}
                />
                <button
                  type="button"
                  className={cls(
                    "dg-process-row",
                    isTrackedNode(item, trackedNodeIds) && "tracked",
                    isSameNode(item, selectedNodeId) && "active"
                  )}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect({ type: "data-processing-item", card, node: item });
                  }}
                >
                  <span>DPI</span>
                  {compact(getNodeName(item), 28)}
                  <small>{isTrackedNode(item, trackedNodeIds) ? "trace" : ""}</small>
                </button>
                <DirectionToggle
                  direction="downstream"
                  expanded={isDirectionExpanded(item, "downstream")}
                  loading={isNodeLoading(item)}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect({ type: "data-processing-item", card, node: item });
                    onToggleLineageDirection(item, "downstream");
                  }}
                />
              </div>
            ))
          ) : (
            <div className="dg-empty-inline">No processing items returned</div>
          )}
        </div>
      )}
    </div>
  );
}

function DirectionHeader() {
  return (
    <div className="dg-direction">
      <strong>Golden Sources</strong>
      <span />
      <strong>Usage final</strong>
    </div>
  );
}

function ConnectorLayer({ links, positions }) {
  return (
    <svg className="dg-links">
      <defs>
        <marker id="dg-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
          <path d="M0,0 L10,5 L0,10 Z" />
        </marker>
      </defs>
      {links.map((link) => {
        const source = positions.get(link.source);
        const target = positions.get(link.target);
        if (!source || !target) return null;
        const x1 = source.x + CARD_WIDTH;
        const y1 = source.y + Math.min(source.height / 2, 76);
        const x2 = target.x;
        const y2 = target.y + Math.min(target.height / 2, 76);
        const mid = (x1 + x2) / 2;
        return (
          <path
            key={link.id}
            className={cls(
              "dg-link",
              link.relation === "IS_INPUT_OF" && "input",
              link.relation === "IS_OUTPUT_OF" && "output",
              link.warning && "warning"
            )}
            d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
          />
        );
      })}
    </svg>
  );
}

function DetailsDrawer({ selected, qualityItems, summary, agentState, onAskAgent }) {
  if (!selected) {
    return (
      <aside className="dg-details">
        <div className="dg-details-empty">
          <strong>No asset selected</strong>
          <span>Select a source, process, dataset, field, or usage card to inspect lineage context.</span>
        </div>
      </aside>
    );
  }

  const node = selected.node;
  const props = node?.properties || {};
  const quality = qualityItems || [];
  const firstQuality = quality[0] || {};
  const rows = [
    ["Asset type", node?.type || selected.card?.kind],
    ["node_id", node?.node_id || node?.id],
    ["Technical path", getNodePath(node)],
    ["app_code", props.app_code || props.application_code || firstQuality.application_code_norm],
    ["Quality score", firstQuality.control_score ?? firstQuality.quality_score],
    ["KO rate", quality.length ? `${Math.round(koRate(firstQuality) * 1000) / 10}%` : ""],
  ];

  return (
    <aside className="dg-details">
      <div className="dg-details-head">
        <span className={cls("dg-asset-icon", classifyAssetType(node?.type))}>{assetIcon(node?.type)}</span>
        <div>
          <h2>{getNodeName(node)}</h2>
          <p>{selected.type}</p>
        </div>
      </div>

      <div className="dg-detail-section">
        {rows.map(([label, value]) => (
          <div key={label} className="dg-detail-row">
            <span>{label}</span>
            <code>{value || "-"}</code>
          </div>
        ))}
      </div>

      <div className="dg-detail-section">
        <h3>Lineage summary</h3>
        <div className="dg-summary-grid">
          <span>Upstream <strong>{summary.upstream}</strong></span>
          <span>Downstream <strong>{summary.downstream}</strong></span>
        </div>
      </div>

      <div className="dg-detail-section">
        <div className="dg-section-title">
          <h3>Related DQC checks</h3>
          <button type="button" onClick={onAskAgent} disabled={agentState.loading}>
            {agentState.loading ? "Asking..." : "Ask Agent"}
          </button>
        </div>
        <DqcControls items={quality} compact />
        {agentState.error && <div className="dg-error">{agentState.error}</div>}
        {agentState.answer && (
          <div className="dg-agent-answer">
            {agentState.answer.explanation || agentState.answer.answer || JSON.stringify(agentState.answer, null, 2)}
          </div>
        )}
      </div>
    </aside>
  );
}

function DqcControls({ items, compact: small = false }) {
  if (!items?.length) return <p className="dg-muted">No DQC controls matched this asset.</p>;
  return (
    <div className={cls("dg-controls", small && "compact")}>
      {items.slice(0, small ? 4 : 12).map((item, index) => (
        <div key={item.check_id || item.id || index} className={cls("dg-control-card", badgeFromDqc([item])?.tone)}>
          <span className="dg-control-source">{item.control_source || "DQC"}</span>
          <strong>{item.control_name || item.quality_dimension || item.failure_reason || "Quality control"}</strong>
          <div>
            <span>Field / Dimension</span>
            <code>{item.field || item.quality_dimension || item.controlled_object_type || "-"}</code>
          </div>
          <div>
            <span>Score / Status</span>
            <code>{item.score ?? item.quality_score ?? item.control_score ?? "-"} / {item.status || item.control_status || item.confidence_level || "-"}</code>
          </div>
          <div>
            <span>Counts</span>
            <code>
              OK {item.ok_count ?? "-"} / KO {item.ko_count ?? "-"} / Total {item.controlled_item_count ?? "-"}
            </code>
          </div>
          <div>
            <span>Match</span>
            <code>
              {item.match_method || "-"} / {item.confidence_level || "-"} /{" "}
              {item.human_review_required ? "human review" : item.reviewed ? "reviewed" : "not reviewed"}
            </code>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function LineageExplorer() {
  const canvasRef = useRef(null);
  const workspaceRef = useRef(null);
  const cardDragRef = useRef({ active: false, cardId: null, startX: 0, startY: 0, originX: 0, originY: 0, moved: false });
  const panRef = useRef({ active: false, startX: 0, startY: 0, scrollLeft: 0, scrollTop: 0 });
  const suppressClickRef = useRef({ cardId: null, until: 0 });
  const [nodeId, setNodeId] = useState("");
  const [assetType, setAssetType] = useState("all");
  const [lineageScope, setLineageScope] = useState("end_to_end");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [showBadges, setShowBadges] = useState(true);
  const [searchText, setSearchText] = useState("");
  const [canvasSearch, setCanvasSearch] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [graph, setGraph] = useState(null);
  const [activePath, setActivePath] = useState(null);
  const [viewMode, setViewMode] = useState("datagalaxy");
  const [resolvedDqc, setResolvedDqc] = useState([]);
  const [unresolvedDqc, setUnresolvedDqc] = useState([]);
  const [expandedCards, setExpandedCards] = useState({});
  const [expandedNodeIds, setExpandedNodeIds] = useState(() => new Set());
  const [lineageLoadingIds, setLineageLoadingIds] = useState(() => new Set());
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [qualityLoading, setQualityLoading] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [manualPositions, setManualPositions] = useState(() => new Map());
  const [isPanning, setIsPanning] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [draggingCardId, setDraggingCardId] = useState(null);
  const [agentState, setAgentState] = useState({ loading: false, answer: null, error: "" });

  const qualityByKey = useMemo(() => {
    const index = new Map();
    [...resolvedDqc, ...unresolvedDqc.map((item) => ({ ...item, __unresolved: true }))].forEach((item) => {
      dqcKeys(item).forEach((key) => {
        if (!index.has(key)) index.set(key, []);
        index.get(key).push(item);
      });
    });
    return index;
  }, [resolvedDqc, unresolvedDqc]);

  const board = useMemo(() => buildCards(graph, qualityByKey), [graph, qualityByKey]);
  const layout = useMemo(
    () =>
      enrichLayout(board.cards, board.links, expandedCards, expandedNodeIds, graph?.root, {
        assetType,
        canvasSearch,
        issuesOnly,
      }, manualPositions),
    [board.cards, board.links, expandedCards, expandedNodeIds, graph?.root, assetType, canvasSearch, issuesOnly, manualPositions]
  );
  const datagalaxyStats = useMemo(() => {
    if (!graph) return { cards: 0, links: 0 };
    return {
      cards: groupNodesIntoCards(graph.nodes, graph.edges).cards.length,
      links: deriveVisualLineageEdges(graph.nodes, graph.edges).length,
    };
  }, [graph]);
  const visibleStats =
    viewMode === "datagalaxy"
      ? datagalaxyStats
      : {
          cards: layout.columns.reduce((sum, col) => sum + col.cards.length, 0),
          links: layout.links.length,
        };

  const selectedQuality = useMemo(
    () => (selected?.node ? collectQualityForNode(selected.node, qualityByKey) : []),
    [selected, qualityByKey]
  );

  const trackedNodeIds = useMemo(() => {
    const ids = new Set();
    if (graph?.root) ids.add(String(graph.root));
    if (selected?.node?.id) ids.add(String(selected.node.id));
    if (selected?.node?.node_id) ids.add(String(selected.node.node_id));

    return ids;
  }, [graph, selected]);

  const lineageSummary = useMemo(() => {
    if (!selected?.card) return { upstream: 0, downstream: 0 };
    return {
      upstream: layout.links.filter((link) => link.target === selected.card.id).length,
      downstream: layout.links.filter((link) => link.source === selected.card.id).length,
    };
  }, [selected, layout.links]);

  async function loadDqc() {
    setQualityLoading(true);
    try {
      const [resolved, unresolved] = await Promise.allSettled([fetchResolvedDqc(1000), fetchUnresolvedDqc(1000)]);
      if (resolved.status === "fulfilled") setResolvedDqc(safeItems(resolved.value));
      if (unresolved.status === "fulfilled") setUnresolvedDqc(safeItems(unresolved.value));
    } finally {
      setQualityLoading(false);
    }
  }

  useEffect(() => {
    loadDqc();
  }, []);

  useEffect(() => {
    if (!graph?.root || !board.cards.length || Object.keys(expandedCards).length > 0) return;

    const root = String(graph.root);
    const card = board.cards.find((candidate) => cardContainsNode(candidate, root));
    if (!card) return;

    const fieldParent = card.structures.find((structure) =>
      (card.fieldsByStructure.get(structure.id) || []).some((field) => canonicalNodeId(field) === root)
    );
    const isProcessItemRoot = (card.processItems || []).some((item) => canonicalNodeId(item) === root);

    if (fieldParent || card.structures.length || isProcessItemRoot) {
      setExpandedCards({
        [card.id]: {
          structures: Boolean(fieldParent || card.structures.length),
          fieldIds: fieldParent ? new Set([fieldParent.id]) : new Set(),
          items: isProcessItemRoot,
        },
      });
    }
  }, [graph?.root, board.cards, expandedCards]);

  useEffect(() => {
    function handlePointerMove(event) {
      if (cardDragRef.current.active) {
        event.preventDefault();
        const dx = (event.clientX - cardDragRef.current.startX) / Math.max(zoom, 0.1);
        const dy = (event.clientY - cardDragRef.current.startY) / Math.max(zoom, 0.1);
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) cardDragRef.current.moved = true;
        setManualPositions((current) => {
          const next = new Map(current);
          next.set(cardDragRef.current.cardId, {
            x: Math.max(12, cardDragRef.current.originX + dx),
            y: Math.max(84, cardDragRef.current.originY + dy),
          });
          return next;
        });
        return;
      }

      if (panRef.current.active && canvasRef.current) {
        event.preventDefault();
        canvasRef.current.scrollLeft = panRef.current.scrollLeft - (event.clientX - panRef.current.startX);
        canvasRef.current.scrollTop = panRef.current.scrollTop - (event.clientY - panRef.current.startY);
      }
    }

    function handlePointerUp() {
      if (cardDragRef.current.active) {
        if (cardDragRef.current.moved) {
          suppressClickRef.current = {
            cardId: cardDragRef.current.cardId,
            until: Date.now() + 220,
          };
        }
        cardDragRef.current.active = false;
        setDraggingCardId(null);
      }
      if (panRef.current.active) {
        panRef.current.active = false;
        setIsPanning(false);
      }
    }

    window.addEventListener("mousemove", handlePointerMove);
    window.addEventListener("mouseup", handlePointerUp);
    return () => {
      window.removeEventListener("mousemove", handlePointerMove);
      window.removeEventListener("mouseup", handlePointerUp);
    };
  }, [zoom]);

  useEffect(() => {
    function syncFullscreenState() {
      setIsFullscreen(document.fullscreenElement === workspaceRef.current);
    }

    document.addEventListener("fullscreenchange", syncFullscreenState);
    return () => document.removeEventListener("fullscreenchange", syncFullscreenState);
  }, []);

  async function handleSearch() {
    if (!searchText.trim()) return;
    setError("");
    try {
      const data = await searchAssets(searchText.trim(), 12);
      setSearchResults(safeItems(data));
    } catch (err) {
      setError(err.message || "Search failed. Check backend availability.");
    }
  }

  async function loadLineage(id = nodeId, scope = lineageScope) {
    if (!id.trim()) {
      setError("Enter a node_id or choose a search result first.");
      return;
    }
    setLoading(true);
    setError("");
    setGraph(null);
    setActivePath(null);
    setSelected(null);
    setExpandedCards({});
    setExpandedNodeIds(new Set());
    setLineageLoadingIds(new Set());
    setManualPositions(new Map());
    setAgentState({ loading: false, answer: null, error: "" });
    try {
      const depth = scope === "end_to_end" ? 3 : 1;
      const data = await fetchBusinessLineage(id.trim(), depth);
      const loadedGraph = {
        root: data?.root,
        nodes: Array.isArray(data?.nodes) ? data.nodes : [],
        edges: Array.isArray(data?.edges) ? data.edges : [],
      };
      setGraph(loadedGraph);
      setActivePath(
        Array.isArray(data?.activePath)
          ? data.activePath
          : computeFocusedLineagePath({
              nodes: loadedGraph.nodes,
              edges: loadedGraph.edges,
              focusNodeId: loadedGraph.root || id.trim(),
              startNodeId: loadedGraph.root || id.trim(),
            })
      );
      await loadDqc();
    } catch (err) {
      setError(err.message || "Failed to load lineage. Check the node_id and backend.");
    } finally {
      setLoading(false);
    }
  }

  function selectSearchResult(result) {
    const id = searchResultNodeId(result);
    if (!id) return;
    setNodeId(id);
    setSearchText(result.name || result.technical_name || result.label || id);
    setSearchResults([]);
    loadLineage(id);
  }

  function loadDemoLineage() {
    setError("");
    setLoading(false);
    setGraph({
      root: mockLineageGraph.root,
      nodes: mockLineageGraph.nodes,
      edges: mockLineageGraph.edges,
    });
    setActivePath(mockLineageGraph.activePath);
    setSelected(null);
    setExpandedCards({});
    setExpandedNodeIds(new Set());
    setLineageLoadingIds(new Set());
    setManualPositions(new Map());
    setAgentState({ loading: false, answer: null, error: "" });
    setNodeId(mockLineageGraph.root);
    setSearchText("Demo customer lineage");
    setViewMode("datagalaxy");
  }

  function toggleSource(cardId) {
    setExpandedCards((current) => ({
      ...current,
      [cardId]: {
        structures: !current[cardId]?.structures,
        fieldIds: current[cardId]?.fieldIds || new Set(),
      },
    }));
  }

  function toggleStructure(cardId, structureId) {
    setExpandedCards((current) => {
      const currentCard = current[cardId] || { structures: true, fieldIds: new Set() };
      const fieldIds = new Set(currentCard.fieldIds || []);
      if (fieldIds.has(structureId)) fieldIds.delete(structureId);
      else fieldIds.add(structureId);
      return {
        ...current,
        [cardId]: { structures: true, fieldIds },
      };
    });
  }

  function toggleCardItems(cardId) {
    setExpandedCards((current) => ({
      ...current,
      [cardId]: {
        ...(current[cardId] || {}),
        items: !current[cardId]?.items,
      },
    }));
  }

  function isLineageNodeExpanded(node) {
    const id = canonicalNodeId(node);
    return Boolean(
      id &&
        (hasLineageExpansion(expandedNodeIds, id, "upstream") ||
          hasLineageExpansion(expandedNodeIds, id, "downstream"))
    );
  }

  function isLineageDirectionExpanded(node, direction) {
    const id = canonicalNodeId(node);
    return Boolean(id && hasLineageExpansion(expandedNodeIds, id, direction));
  }

  function isLineageNodeLoading(node) {
    const id = canonicalNodeId(node);
    return Boolean(id && lineageLoadingIds.has(id));
  }

  async function toggleLineageNode(node, direction = "both") {
    const id = canonicalNodeId(node);
    if (!id) return;
    const keys =
      direction === "both"
        ? [lineageExpansionKey(id, "upstream"), lineageExpansionKey(id, "downstream")]
        : [lineageExpansionKey(id, direction)];
    const expanded = keys.every((key) => expandedNodeIds.has(key));

    if (expanded) {
      setExpandedNodeIds((current) => {
        const next = new Set(current);
        keys.forEach((key) => next.delete(key));
        return next;
      });
      return;
    }

    setExpandedNodeIds((current) => {
      const next = new Set(current);
      keys.forEach((key) => next.add(key));
      return next;
    });

    if (lineageLoadingIds.has(id)) return;
    setLineageLoadingIds((current) => new Set(current).add(id));
    try {
      const data = await fetchBusinessLineage(id, lineageScope === "end_to_end" ? 3 : 2);
      setGraph((current) => mergeGraphs(current, data));
    } catch (err) {
      setError(err.message || `Failed to expand lineage for ${getNodeName(node)}.`);
    } finally {
      setLineageLoadingIds((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    }
  }

  async function toggleDatagalaxyItem(rawNode, item, card) {
    const id = canonicalNodeId(rawNode || item);
    if (!id) return;

    setSelected({ type: item?.kind || "item", card: { id: card?.id }, node: rawNode });

    const keys = [lineageExpansionKey(id, "upstream"), lineageExpansionKey(id, "downstream")];
    const expanded = keys.every((key) => expandedNodeIds.has(key));
    if (expanded) {
      setExpandedNodeIds((current) => {
        const next = new Set(current);
        keys.forEach((key) => next.delete(key));
        return next;
      });
      setActivePath(null);
      return;
    }

    setExpandedNodeIds((current) => {
      const next = new Set(current);
      keys.forEach((key) => next.add(key));
      return next;
    });

    const currentGraph = graph || { root: id, nodes: [], edges: [] };
    const immediatePath = computeFocusedLineagePath({
      nodes: currentGraph.nodes,
      edges: currentGraph.edges,
      focusNodeId: id,
      startNodeId: currentGraph.root,
    });
    setActivePath(immediatePath.length ? immediatePath : [id]);

    if (lineageLoadingIds.has(id)) return;
    setLineageLoadingIds((current) => new Set(current).add(id));
    try {
      const data = await fetchBusinessLineage(id, lineageScope === "end_to_end" ? 3 : 2);
      setGraph((current) => {
        const merged = mergeGraphs(current, data);
        setActivePath(
          computeFocusedLineagePath({
            nodes: merged.nodes,
            edges: merged.edges,
            focusNodeId: id,
            startNodeId: merged.root || currentGraph.root,
          })
        );
        return merged;
      });
    } catch (err) {
      setError(err.message || `Failed to expand lineage for ${item?.displayName || getNodeName(rawNode)}.`);
    } finally {
      setLineageLoadingIds((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    }
  }

  function fitBoard() {
    setZoom(0.82);
  }

  function startCardDrag(event, cardId) {
    if (event.button !== 0) return;
    const position = layout.positions.get(cardId);
    if (!position) return;
    cardDragRef.current = {
      active: true,
      cardId,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
      moved: false,
    };
    setDraggingCardId(cardId);
  }

  function blockDragClick(event, cardId) {
    if (suppressClickRef.current.cardId === cardId && Date.now() < suppressClickRef.current.until) {
      event.preventDefault();
      event.stopPropagation();
    }
  }

  function startCanvasPan(event) {
    if (event.button !== 2 || !canvasRef.current) return;
    event.preventDefault();
    panRef.current = {
      active: true,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: canvasRef.current.scrollLeft,
      scrollTop: canvasRef.current.scrollTop,
    };
    setIsPanning(true);
  }

  async function toggleFullscreen() {
    if (!workspaceRef.current) return;
    if (document.fullscreenElement === workspaceRef.current) {
      await document.exitFullscreen();
      return;
    }
    await workspaceRef.current.requestFullscreen();
  }

  async function askAgentForSelected() {
    if (!selected?.node) return;
    setAgentState({ loading: true, answer: null, error: "" });
    try {
      const prompt = `Investigate quality and lineage context for asset ${getNodeName(selected.node)} with node_id ${
        selected.node.node_id || selected.node.id
      }. Explain issues and suggested actions.`;
      const answer = await askDqcAgent(prompt);
      setAgentState({ loading: false, answer, error: "" });
    } catch (err) {
      setAgentState({ loading: false, answer: null, error: err.message || "Agent request failed." });
    }
  }

  const selectedId = selected?.card?.id;
  const selectedNodeId = selected?.node?.id || selected?.node?.node_id;

  return (
    <div className="dg-lineage-page">
      <aside className="dg-mini-toolbar" aria-label="Lineage navigation tools">
        <button type="button" title="Search">SEA</button>
        <button type="button" className="active" title="Lineage">LIN</button>
        <button type="button" title="Quality">QLT</button>
        <button type="button" title="Review">REV</button>
      </aside>

      <aside className="dg-filter-panel">
        <div className="dg-panel-brand">
          <strong>Lineage Explorer</strong>
          <span>DataGalaxy-style asset view</span>
        </div>

        <label>Search asset name or node_id</label>
        <div className="dg-search-row">
          <input
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") handleSearch();
            }}
            placeholder="Customer, table, node_id..."
          />
          <button type="button" onClick={handleSearch}>Go</button>
        </div>

        {searchResults.length > 0 && (
          <div className="dg-search-results">
            {searchResults.map((result, index) => (
              <button key={result.id || result.node_id || index} type="button" onClick={() => selectSearchResult(result)}>
                <strong>{result.name || result.technical_name || result.label || "Unnamed asset"}</strong>
                <span>{result.type || "asset"}</span>
                <code>{result.path || result.node_id || result.id}</code>
              </button>
            ))}
          </div>
        )}

        <label>Direct lineage root</label>
        <textarea
          value={nodeId}
          rows={3}
          onChange={(event) => setNodeId(event.target.value)}
          placeholder="Paste node_id..."
        />

        <label>Lineage scope</label>
        <select value={lineageScope} onChange={(event) => setLineageScope(event.target.value)}>
          <option value="end_to_end">End-to-end source to usage</option>
          <option value="direct">Direct neighborhood</option>
        </select>

        <label>Filter by asset type</label>
        <select value={assetType} onChange={(event) => setAssetType(event.target.value)}>
          <option value="all">All assets</option>
          <option value="source">Sources</option>
          <option value="process">Processes</option>
          <option value="dataset">Datasets</option>
          <option value="structure">Tables</option>
          <option value="usage">Usage</option>
        </select>

        <label>Filter visible canvas</label>
        <input
          value={canvasSearch}
          onChange={(event) => setCanvasSearch(event.target.value)}
          placeholder="Filter loaded cards..."
        />

        <div className="dg-toggle">
          <input
            id="issues-only"
            type="checkbox"
            checked={issuesOnly}
            onChange={(event) => setIssuesOnly(event.target.checked)}
          />
          <label htmlFor="issues-only">Show quality issues only</label>
        </div>
        <div className="dg-toggle">
          <input
            id="show-badges"
            type="checkbox"
            checked={showBadges}
            onChange={(event) => setShowBadges(event.target.checked)}
          />
          <label htmlFor="show-badges">Show DQC badges</label>
        </div>

        <button className="dg-primary" type="button" onClick={() => loadLineage()} disabled={loading}>
          {loading ? "Loading lineage..." : "Load lineage"}
        </button>
        <button className="dg-secondary" type="button" onClick={loadDemoLineage}>
          Load demo
        </button>

        <div className="dg-side-stats">
          <span>Cards <strong>{visibleStats.cards}</strong></span>
          <span>Links <strong>{visibleStats.links}</strong></span>
          <span>DQC <strong>{resolvedDqc.length + unresolvedDqc.length}</strong></span>
        </div>
        {qualityLoading && <p className="dg-muted">Refreshing DQC overlays...</p>}
        {error && <div className="dg-error">{error}</div>}
      </aside>

      <main className="dg-lineage-main" ref={workspaceRef}>
        <header className="dg-canvas-toolbar">
          <div>
            <strong>Source to target lineage</strong>
            <span>Use the left and right controls to expand upstream or downstream from the selected entity.</span>
          </div>
          <div className="dg-toolbar-actions">
            <div className="dg-view-toggle" aria-label="Lineage view mode">
              <button
                type="button"
                className={viewMode === "graph" ? "active" : ""}
                onClick={() => setViewMode("graph")}
              >
                Graph view
              </button>
              <button
                type="button"
                className={viewMode === "datagalaxy" ? "active" : ""}
                onClick={() => setViewMode("datagalaxy")}
              >
                DataGalaxy view
              </button>
            </div>
            <div className="dg-zoom-controls">
              {viewMode === "graph" && (
                <>
                  <button type="button" onClick={() => setZoom((value) => Math.max(0.65, value - 0.1))}>-</button>
                  <button type="button" onClick={fitBoard}>Fit</button>
                  <button type="button" onClick={() => setZoom((value) => Math.min(1.25, value + 0.1))}>+</button>
                </>
              )}
              <button type="button" onClick={toggleFullscreen}>{isFullscreen ? "Exit" : "Full"}</button>
            </div>
          </div>
        </header>

        <section
          className={cls("dg-canvas", isPanning && "panning")}
          ref={canvasRef}
          onMouseDown={startCanvasPan}
          onContextMenu={(event) => event.preventDefault()}
        >
          {!graph && !loading && (
            <div className="dg-empty-state">
              <strong>Search for an asset to begin</strong>
              <span>The canvas starts from the selected entity and grows only through your expansions.</span>
            </div>
          )}
          {loading && (
            <div className="dg-empty-state">
              <strong>Loading lineage</strong>
              <span>Fetching graph and DQC overlays from the backend.</span>
            </div>
          )}
          {graph && layout.columns.every((column) => column.cards.length === 0) && (
            <div className="dg-empty-state">
              <strong>No lineage cards match the filters</strong>
              <span>Clear filters or load another root asset.</span>
            </div>
          )}

          {graph && viewMode === "datagalaxy" && (
            <LineageDatagalaxyView
              nodes={graph.nodes}
              edges={graph.edges}
              activePath={activePath}
              startNodeId={graph.root}
              endNodeId={selected?.node?.id || selected?.node?.node_id}
              showDqc={showBadges}
              issuesOnly={issuesOnly}
              getDqcItems={(rawNode) => collectQualityForNode(rawNode, qualityByKey)}
              rowExpansion={{
                expanded: (rawNode) => isLineageNodeExpanded(rawNode),
                loading: (rawNode) => isLineageNodeLoading(rawNode),
              }}
              onNodeClick={(rawNode, card) => {
                setSelected({ type: card?.kind || "card", card: { id: card?.id }, node: rawNode });
              }}
              onItemClick={(rawNode, item, card) => {
                setSelected({ type: item?.kind || "item", card: { id: card?.id }, node: rawNode });
              }}
              onItemExpand={toggleDatagalaxyItem}
            />
          )}

          {graph && viewMode === "graph" && (
            <div
              className="dg-board"
              style={{
                width: layout.boardWidth,
                height: layout.boardHeight,
                transform: `scale(${zoom})`,
              }}
            >
              <DirectionHeader />
              <ConnectorLayer links={layout.links} positions={layout.positions} />
              {layout.columns.flatMap((column) =>
                column.cards.map((card) => {
                  const position = layout.positions.get(card.id);
                  const style = { left: position.x, top: position.y, width: CARD_WIDTH };
                  if (card.kind === "source" || (card.kind === "structure" && card.structures.length > 0)) {
                    return (
                      <div
                        key={card.id}
                        className={cls("dg-card-position", draggingCardId === card.id && "dragging")}
                        style={style}
                        onMouseDown={(event) => startCardDrag(event, card.id)}
                        onClickCapture={(event) => blockDragClick(event, card.id)}
                      >
                        <SourceCard
                          card={card}
                          expanded={expandedCards[card.id]}
                          selectedId={selectedId}
                          selectedNodeId={selectedNodeId}
                          showBadges={showBadges}
                          trackedNodeIds={trackedNodeIds}
                          onSelect={setSelected}
                          onToggleSource={toggleSource}
                          onToggleStructure={toggleStructure}
                          onToggleLineageNode={toggleLineageNode}
                          onToggleLineageDirection={toggleLineageNode}
                          isDirectionExpanded={isLineageDirectionExpanded}
                          isNodeExpanded={isLineageNodeExpanded}
                          isNodeLoading={isLineageNodeLoading}
                        />
                      </div>
                    );
                  }
                  return (
                    <div
                      key={card.id}
                      className={cls("dg-card-position", draggingCardId === card.id && "dragging")}
                      style={style}
                      onMouseDown={(event) => startCardDrag(event, card.id)}
                      onClickCapture={(event) => blockDragClick(event, card.id)}
                    >
                      <AssetCard
                        card={card}
                        expanded={expandedCards[card.id]}
                        selectedId={selectedId}
                        selectedNodeId={selectedNodeId}
                        showBadges={showBadges}
                        trackedNodeIds={trackedNodeIds}
                        onSelect={setSelected}
                        onToggleCard={toggleCardItems}
                        onToggleLineageNode={toggleLineageNode}
                        onToggleLineageDirection={toggleLineageNode}
                        isDirectionExpanded={isLineageDirectionExpanded}
                        isNodeExpanded={isLineageNodeExpanded}
                        isNodeLoading={isLineageNodeLoading}
                      />
                    </div>
                  );
                })
              )}
            </div>
          )}
        </section>

        <section className="dg-bottom-dqc">
          <div className="dg-section-title">
            <h3>Selected asset DQC controls</h3>
            <span>{selected ? getNodeName(selected.node) : "No asset selected"}</span>
          </div>
          <DqcControls items={selectedQuality} />
        </section>
      </main>

      <DetailsDrawer
        selected={selected}
        qualityItems={selectedQuality}
        summary={lineageSummary}
        agentState={agentState}
        onAskAgent={askAgentForSelected}
      />
    </div>
  );
}
