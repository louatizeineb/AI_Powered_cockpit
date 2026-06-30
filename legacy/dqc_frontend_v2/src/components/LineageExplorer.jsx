import React, { useEffect, useMemo, useRef, useState } from "react";

import {
  approveDqcMatch,
  askDqcAgent,
  fetchBusinessLineage,
  fetchResolvedDqc,
  fetchUnresolvedDqc,
  rejectDqcMatch,
  searchAssets,
} from "../api";

const LINEAGE_TYPES = new Set([
  "source",
  "container",
  "structure",
  "field",
  "usage",
  "process",
  "pipeline",
  "job",
  "table",
  "dataset",
  "application",
  "app",
  "report",
  "dashboard",
  "dashb",
  "database",
  "db",
  "dataprocessing",
  "dataprocessingitem",
  "data processing",
  "data processing item",
  "traitement",
  "element de traitement",
  "élément de traitement",
]);

const FIELD_TYPES = new Set(["field", "usfield", "dataprocessingitem", "data processing item"]);
const CARD_WIDTH = 280;
const CARD_GAP_X = 170;
const CARD_TOP = 74;
const CARD_LEFT = 52;
const CARD_HEIGHT = 86;
const FIELD_ROW_HEIGHT = 30;

function cls(...items) {
  return items.filter(Boolean).join(" ");
}

function normalizeType(type) {
  return String(type || "entity").toLowerCase();
}

function entityName(node) {
  const props = node?.properties || {};
  return (
    node?.label ||
    props.name_label ||
    props.name ||
    props.name_tech ||
    props.technical_name ||
    props.usage_name ||
    props.usage_tech_name ||
    props.data_processing_name ||
    props.data_processing_item_name ||
    node?.node_id ||
    node?.id ||
    "Unnamed entity"
  );
}

function entitySubtitle(node) {
  const props = node?.properties || {};
  return (
    props.path_full ||
    props.path ||
    props.usage_path ||
    props.container_name ||
    props.source_name ||
    props.domain ||
    props.source_path ||
    props.target_path ||
    node?.node_id ||
    node?.id ||
    ""
  );
}

function iconLabel(type) {
  const normalized = normalizeType(type);
  if (normalized.includes("source") || normalized === "db" || normalized.includes("database")) return "DB";
  if (normalized.includes("container")) return "Cn";
  if (normalized.includes("structure") || normalized.includes("table") || normalized.includes("dataset")) return "Tb";
  if (normalized.includes("processingitem") || normalized.includes("processing item") || normalized.includes("élément")) return "DPI";
  if (normalized.includes("process") || normalized.includes("pipeline") || normalized.includes("job") || normalized.includes("traitement")) return "DP";
  if (normalized.includes("usage") || normalized.includes("report") || normalized.includes("dashboard") || normalized.includes("app")) return "Us";
  if (normalized.includes("field")) return "Fd";
  return "En";
}

function cardTone(type, isRoot) {
  if (isRoot) return "focus";
  const normalized = normalizeType(type);
  if (normalized.includes("processingitem") || normalized.includes("processing item") || normalized.includes("élément")) return "item";
  if (normalized.includes("process") || normalized.includes("pipeline") || normalized.includes("job") || normalized.includes("traitement")) return "process";
  if (normalized.includes("usage") || normalized.includes("report") || normalized.includes("dashboard") || normalized.includes("app")) return "usage";
  if (normalized.includes("structure") || normalized.includes("table") || normalized.includes("dataset")) return "structure";
  if (normalized.includes("source") || normalized.includes("container") || normalized.includes("database")) return "source";
  return "neutral";
}

function truncate(value, length = 26) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length - 1)}...` : text;
}

function nodeKeyCandidates(node) {
  const props = node?.properties || {};
  return [
    node?.id,
    node?.node_id,
    props.node_id,
    props.usage_uuid,
    props.id,
  ].filter(Boolean).map(String);
}

function controlTone(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "passed") return "passed";
  if (normalized === "failed") return "failed";
  if (normalized.includes("review")) return "review";
  if (normalized.includes("medium")) return "review";
  return "unknown";
}

function summarizeControls(items = []) {
  if (!items.length) return null;
  const failed = items.filter((item) => item.control_status === "FAILED");
  const passed = items.filter((item) => item.control_status === "PASSED");
  const primary = failed[0] || passed[0] || items[0];
  return {
    ...primary,
    control_count: items.length,
    failed_count: failed.length,
    passed_count: passed.length,
    control_status: failed.length ? "FAILED" : primary.control_status,
  };
}

function assetFamily(node) {
  const type = normalizeType(node?.type);
  if (type.includes("source") || type.includes("container") || type.includes("database")) return "Source";
  if (type.includes("field")) return "Field";
  if (type.includes("process") || type.includes("pipeline") || type.includes("job") || type.includes("traitement")) return "Process";
  if (type.includes("usage") || type.includes("dashboard") || type.includes("report") || type.includes("api") || type.includes("app")) return "Usage";
  if (type.includes("structure") || type.includes("table") || type.includes("dataset")) return "Structure";
  return "Structure";
}

function qualityFamily(control) {
  if (!control) return "Unknown";
  if (control.control_status === "FAILED") return "Critical";
  if (control.confidence_level === "MEDIUM" || control.human_review_required) return "Needs review";
  if (control.control_status === "PASSED") return "Good";
  return "Warning";
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "-";
  return `${Number(value).toFixed(Number(value) % 1 === 0 ? 0 : 2)}%`;
}

function isLineageNode(node) {
  const type = normalizeType(node?.type);
  if (type.includes("term") || type.includes("glossary")) return false;
  return LINEAGE_TYPES.has(type) || [...LINEAGE_TYPES].some((item) => type.includes(item));
}

function isFieldNode(node) {
  const type = normalizeType(node?.type);
  return FIELD_TYPES.has(type) || type.includes("field") || type.includes("processingitem") || type.includes("processing item");
}

function isSourceNode(node) {
  return assetFamily(node) === "Source";
}

function isStructureNode(node) {
  return assetFamily(node) === "Structure";
}

function edgeLabel(edge) {
  const raw = String(edge?.type || edge?.properties?.link_type || "lineage");
  return raw.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
}

function getCardHeight(node, fieldsByParent, expandedIds, structuresBySource, expandedStructureIds) {
  const fields = fieldsByParent.get(node.id) || [];
  if (!expandedIds.has(node.id)) return CARD_HEIGHT;
  const structures = structuresBySource.get(node.id) || [];
  if (structures.length) {
    const expandedFieldRows = structures.reduce((sum, structure) => {
      if (!expandedStructureIds.has(structure.id)) return sum;
      return sum + Math.max(fieldsByParent.get(structure.id)?.length || 0, 1) * FIELD_ROW_HEIGHT;
    }, 0);
    return CARD_HEIGHT + 28 + structures.length * 34 + expandedFieldRows;
  }
  return CARD_HEIGHT + 28 + Math.max(fields.length, 1) * FIELD_ROW_HEIGHT;
}

function graphToBoard(graph, expandedIds, visibleIds, manualPositions, expandedStructureIds) {
  const rawNodes = graph?.nodes || [];
  const rawEdges = graph?.edges || [];
  const lineageNodes = rawNodes.filter(isLineageNode);
  const nodeMap = new Map(lineageNodes.map((node) => [node.id, node]));
  const rootId = nodeMap.has(graph?.root) ? graph.root : lineageNodes[0]?.id;

  const lineageEdges = rawEdges.filter((edge) => nodeMap.has(edge.source) && nodeMap.has(edge.target));
  const rawOutgoing = new Map();
  const rawIncoming = new Map();
  for (const edge of lineageEdges) {
    if (!rawOutgoing.has(edge.source)) rawOutgoing.set(edge.source, new Set());
    if (!rawIncoming.has(edge.target)) rawIncoming.set(edge.target, new Set());
    rawOutgoing.get(edge.source).add(edge.target);
    rawIncoming.get(edge.target).add(edge.source);
  }
  const visibleNodeSet = visibleIds?.size ? visibleIds : new Set(lineageNodes.map((node) => node.id));
  const allCardNodes = lineageNodes.filter((node) => !isFieldNode(node) || node.id === rootId);
  const allCardIdSet = new Set(allCardNodes.map((node) => node.id));
  const cardNodes = allCardNodes.filter((node) => visibleNodeSet.has(node.id));
  const cardIdSet = new Set(cardNodes.map((node) => node.id));
  const fieldsByParent = new Map();
  const fieldParent = new Map();
  const structureParent = new Map();
  const structuresBySource = new Map();

  for (const edge of lineageEdges) {
    const source = nodeMap.get(edge.source);
    const target = nodeMap.get(edge.target);
    if (source && target && isFieldNode(source) && !isFieldNode(target)) fieldParent.set(source.id, target.id);
    if (source && target && !isFieldNode(source) && isFieldNode(target)) fieldParent.set(target.id, source.id);
    if (source && target && isSourceNode(source) && isStructureNode(target)) structureParent.set(target.id, source.id);
    if (source && target && isStructureNode(source) && isSourceNode(target)) structureParent.set(source.id, target.id);
  }

  for (const node of lineageNodes.filter(isFieldNode)) {
    const parentId = fieldParent.get(node.id);
    if (!parentId || !cardIdSet.has(parentId)) continue;
    if (!fieldsByParent.has(parentId)) fieldsByParent.set(parentId, []);
    fieldsByParent.get(parentId).push(node);
  }

  for (const node of lineageNodes.filter(isStructureNode)) {
    const parentId = structureParent.get(node.id);
    if (!parentId || !cardIdSet.has(parentId)) continue;
    if (!structuresBySource.has(parentId)) structuresBySource.set(parentId, []);
    structuresBySource.get(parentId).push(node);
  }

  const allOutgoing = new Map();
  const allIncoming = new Map();
  for (const edge of lineageEdges) {
    const source = allCardIdSet.has(edge.source) ? edge.source : fieldParent.get(edge.source);
    const target = allCardIdSet.has(edge.target) ? edge.target : fieldParent.get(edge.target);
    if (!source || !target || source === target || !allCardIdSet.has(source) || !allCardIdSet.has(target)) continue;
    if (!allOutgoing.has(source)) allOutgoing.set(source, new Set());
    if (!allIncoming.has(target)) allIncoming.set(target, new Set());
    allOutgoing.get(source).add(target);
    allIncoming.get(target).add(source);
  }

  const outgoing = new Map(
    [...allOutgoing.entries()]
      .filter(([source]) => visibleNodeSet.has(source))
      .map(([source, targets]) => [source, new Set([...targets].filter((target) => visibleNodeSet.has(target)))])
  );
  const incoming = new Map(
    [...allIncoming.entries()]
      .filter(([target]) => visibleNodeSet.has(target))
      .map(([target, sources]) => [target, new Set([...sources].filter((source) => visibleNodeSet.has(source)))])
  );

  const downstream = new Set();
  const upstream = new Set();
  const walk = (start, adjacency, collector) => {
    const queue = [...(adjacency.get(start) || [])];
    while (queue.length) {
      const id = queue.shift();
      if (!id || id === start || collector.has(id)) continue;
      collector.add(id);
      queue.push(...(adjacency.get(id) || []));
    }
  };

  if (rootId) {
    walk(rootId, outgoing, downstream);
    walk(rootId, incoming, upstream);
  }

  const rootNode = rootId ? nodeMap.get(rootId) : null;
  const upstreamNodes = cardNodes.filter((node) => upstream.has(node.id) && node.id !== rootId);
  const downstreamNodes = cardNodes.filter((node) => downstream.has(node.id) && node.id !== rootId && !upstream.has(node.id));
  const nearbyNodes = cardNodes.filter(
    (node) => node.id !== rootId && !upstream.has(node.id) && !downstream.has(node.id)
  );

  const columns = [
    { id: "upstream", title: "Upstream", nodes: upstreamNodes },
    { id: "focus", title: "Selected entity", nodes: rootNode ? [rootNode] : [] },
    { id: "downstream", title: "Downstream", nodes: [...downstreamNodes, ...nearbyNodes] },
  ].filter((column) => column.nodes.length > 0);

  const positions = new Map();
  const fieldPositions = new Map();
  const cardHeights = new Map();
  columns.forEach((column, columnIndex) => {
    let y = CARD_TOP;
    column.nodes.forEach((node) => {
      const height = getCardHeight(node, fieldsByParent, expandedIds, structuresBySource, expandedStructureIds);
      cardHeights.set(node.id, height);
      positions.set(node.id, {
        x: manualPositions.get(node.id)?.x ?? CARD_LEFT + columnIndex * (CARD_WIDTH + CARD_GAP_X),
        y: manualPositions.get(node.id)?.y ?? y,
        column: column.id,
      });
      if (expandedIds.has(node.id)) {
        const fields = fieldsByParent.get(node.id) || [];
        fields.forEach((field, fieldIndex) => {
          fieldPositions.set(field.id, {
            x: CARD_LEFT + columnIndex * (CARD_WIDTH + CARD_GAP_X) + 58,
            y: y + CARD_HEIGHT + 20 + fieldIndex * FIELD_ROW_HEIGHT,
            parentId: node.id,
          });
        });
      }
      y += height + 42;
    });
  });

  const visibleConnectors = new Map();
  for (const edge of lineageEdges) {
    const sourceParent = fieldParent.get(edge.source);
    const targetParent = fieldParent.get(edge.target);
    const sourceVisible = visibleNodeSet.has(edge.source) || (sourceParent && visibleNodeSet.has(sourceParent));
    const targetVisible = visibleNodeSet.has(edge.target) || (targetParent && visibleNodeSet.has(targetParent));
    if (!sourceVisible || !targetVisible) continue;

    const sourceIsVisibleField = fieldPositions.has(edge.source);
    const targetIsVisibleField = fieldPositions.has(edge.target);
    const source = sourceIsVisibleField ? edge.source : cardIdSet.has(edge.source) ? edge.source : sourceParent;
    const target = targetIsVisibleField ? edge.target : cardIdSet.has(edge.target) ? edge.target : targetParent;

    if (!source || !target || source === target) continue;
    if ((!positions.has(source) && !fieldPositions.has(source)) || (!positions.has(target) && !fieldPositions.has(target))) continue;

    const connectorId = `${source}->${target}->${edge.type}`;
    if (visibleConnectors.has(connectorId)) continue;
    visibleConnectors.set(connectorId, { source, target, edge });
  }

  const connectors = [...visibleConnectors.values()].map(({ source, target, edge }) => {
      const sourcePos = positions.get(source) || fieldPositions.get(source);
      const targetPos = positions.get(target) || fieldPositions.get(target);
      const sourceIsField = fieldPositions.has(source);
      const targetIsField = fieldPositions.has(target);
      const sourceWidth = sourceIsField ? CARD_WIDTH - 68 : CARD_WIDTH;
      return {
        id: `${source}-${target}-${edge.type}`,
        source,
        target,
        label: edgeLabel(edge),
        x1: sourcePos.x + sourceWidth,
        y1: sourcePos.y + (sourceIsField ? FIELD_ROW_HEIGHT / 2 : CARD_HEIGHT / 2),
        x2: targetPos.x,
        y2: targetPos.y + (targetIsField ? FIELD_ROW_HEIGHT / 2 : CARD_HEIGHT / 2),
      };
  });

  const columnHeights = columns.map((column) => {
    const last = column.nodes[column.nodes.length - 1];
    if (!last) return CARD_TOP + CARD_HEIGHT;
    return (positions.get(last)?.y || CARD_TOP) + (cardHeights.get(last.id) || CARD_HEIGHT);
  });

  return {
    columns,
    connectors,
    fieldsByParent,
    structuresBySource,
    fieldParent,
    incoming,
    outgoing,
    allIncoming,
    allOutgoing,
    rawIncoming,
    rawOutgoing,
    lineageEdges,
    nodeMap,
    positions,
    rootId,
    width: CARD_LEFT * 2 + columns.length * CARD_WIDTH + Math.max(columns.length - 1, 0) * CARD_GAP_X,
    height: Math.max(...columnHeights, CARD_TOP + CARD_HEIGHT) + 360,
  };
}

function initialVisibleIds(graph) {
  const nodes = (graph?.nodes || []).filter(isLineageNode);
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const rootId = nodeMap.has(graph?.root) ? graph.root : nodes[0]?.id;
  if (!rootId) return new Set();

  const ids = new Set([rootId]);
  for (const edge of graph?.edges || []) {
    if (edge.source === rootId && nodeMap.has(edge.target)) ids.add(edge.target);
    if (edge.target === rootId && nodeMap.has(edge.source)) ids.add(edge.source);
  }
  return ids;
}

function FieldRows({
  fields = [],
  selectedFieldId,
  qualityByNode,
  controlsVisible,
  onSelectField,
  getParentCount,
  getDescendantCount,
  onRevealParents,
  onRevealDescendants,
}) {
  if (!fields.length) {
    return <div className="lineage-empty-fields">No fields in this lineage slice</div>;
  }

  return (
    <div className="lineage-fields">
      {fields.map((field) => {
        const control = controlsVisible
          ? summarizeControls(nodeKeyCandidates(field).flatMap((key) => qualityByNode.get(key) || []))
          : null;
        return (
          <button
            key={field.id}
            className={cls("lineage-field-row", selectedFieldId === field.id && "active", control && `quality-${controlTone(control.control_status)}`)}
            onClick={(event) => {
              event.stopPropagation();
              onSelectField(field);
            }}
            type="button"
          >
            <span className="field-status" title={control ? `${control.control_status} ${control.control_score ?? ""}%` : "No control result"} />
            <span>{truncate(entityName(field), 30)}</span>
            {control && (
              <span className={cls("lineage-control-chip", controlTone(control.control_status))}>
                {control.control_status}
              </span>
            )}
            <span className="lineage-row-tools">
              {getParentCount(field) > 0 && (
                <span
                  className="lineage-mini-plus"
                  title="Afficher les parents"
                  onClick={(event) => {
                    event.stopPropagation();
                    onRevealParents(field);
                  }}
                >
                  +
                </span>
              )}
              {getDescendantCount(field) > 0 && (
                <span
                  className="lineage-mini-plus"
                  title="Afficher les descendants"
                  onClick={(event) => {
                    event.stopPropagation();
                    onRevealDescendants(field);
                  }}
                >
                  +
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function StructureRows({
  structures = [],
  expandedStructureIds,
  fieldsByParent,
  selectedFieldId,
  qualityByNode,
  controlsVisible,
  onToggleStructure,
  onSelectField,
  getParentCount,
  getDescendantCount,
  onRevealParents,
  onRevealDescendants,
}) {
  if (!structures.length) return null;

  return (
    <div className="lineage-structures">
      {structures.map((structure) => {
        const fields = fieldsByParent.get(structure.id) || [];
        const expanded = expandedStructureIds.has(structure.id);
        return (
          <div key={structure.id} className="lineage-structure-block">
            <button
              className="lineage-structure-row"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onToggleStructure(structure.id);
              }}
            >
              <span>{expanded ? "-" : "+"}</span>
              <strong>{truncate(entityName(structure), 28)}</strong>
              <small>{fields.length} fields</small>
            </button>
            {expanded && (
              <FieldRows
                fields={fields}
                selectedFieldId={selectedFieldId}
                qualityByNode={qualityByNode}
                controlsVisible={controlsVisible}
                onSelectField={onSelectField}
                getParentCount={getParentCount}
                getDescendantCount={getDescendantCount}
                onRevealParents={onRevealParents}
                onRevealDescendants={onRevealDescendants}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function EntityCard({
  node,
  fields,
  structures,
  fieldsByParent,
  expandedStructureIds,
  qualityByNode,
  expanded,
  selected,
  selectedNodeId,
  root,
  position,
  parentCount,
  descendantCount,
  onToggle,
  onSelectField,
  onToggleStructure,
  getFieldParentCount,
  getFieldDescendantCount,
  onRevealParents,
  onRevealDescendants,
  onDragStart,
  wasDragged,
  dimmed,
  controlsVisible,
}) {
  const directControls = nodeKeyCandidates(node).flatMap((key) => qualityByNode.get(key) || []);
  const fieldControls = fields.flatMap((field) =>
    nodeKeyCandidates(field).flatMap((key) => qualityByNode.get(key) || [])
  );
  const structureControls = structures.flatMap((structure) =>
    [
      ...nodeKeyCandidates(structure).flatMap((key) => qualityByNode.get(key) || []),
      ...(fieldsByParent.get(structure.id) || []).flatMap((field) =>
        nodeKeyCandidates(field).flatMap((key) => qualityByNode.get(key) || [])
      ),
    ]
  );
  const control = summarizeControls([...directControls, ...fieldControls, ...structureControls]);

  return (
    <div
      className={cls(
        "lineage-card",
        cardTone(node.type, root),
        selected && "selected",
        expanded && "expanded",
        controlsVisible && control && `quality-${controlTone(control.control_status)}`,
        dimmed && "dimmed"
      )}
      style={{
        left: position.x,
        top: position.y,
        width: CARD_WIDTH,
      }}
      onMouseDown={(event) => onDragStart(event, node)}
      onClick={(event) => {
        if (event.defaultPrevented || wasDragged(node)) return;
        onToggle(node);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onToggle(node);
      }}
      role="button"
      tabIndex={0}
    >
      <div className="lineage-card-main">
        <span className="lineage-icon">
          {iconLabel(node.type)}
          {controlsVisible && control && <span className={cls("lineage-quality-dot", controlTone(control.control_status))} />}
        </span>
        <span className="lineage-copy">
          <small>{truncate(entitySubtitle(node), 32)}</small>
          <strong>{truncate(entityName(node), 24)}</strong>
          {controlsVisible && control && (
            <span className={cls("lineage-control-summary", controlTone(control.control_status))}>
              {control.control_status} · {control.control_score ?? "-"}% · {control.control_count} control{control.control_count > 1 ? "s" : ""}
            </span>
          )}
        </span>
        <span className="lineage-card-tools">
          {parentCount > 0 && (
            <button
              className="lineage-plus left"
              type="button"
              title={`Afficher ${parentCount} parent${parentCount > 1 ? "s" : ""}`}
              onClick={(event) => {
                event.stopPropagation();
                onRevealParents(node);
              }}
            >
              +
            </button>
          )}
          {(fields.length > 0 || structures.length > 0) && (
            <span className="lineage-field-count">{structures.length || fields.length}</span>
          )}
          {descendantCount > 0 && (
            <button
              className="lineage-plus right"
              type="button"
              title={`Afficher ${descendantCount} descendant${descendantCount > 1 ? "s" : ""}`}
              onClick={(event) => {
                event.stopPropagation();
                onRevealDescendants(node);
              }}
            >
              +
            </button>
          )}
        </span>
      </div>

      {expanded && structures.length > 0 && (
        <StructureRows
          structures={structures}
          expandedStructureIds={expandedStructureIds}
          fieldsByParent={fieldsByParent}
          selectedFieldId={selectedNodeId}
          qualityByNode={qualityByNode}
          controlsVisible={controlsVisible}
          onToggleStructure={onToggleStructure}
          onSelectField={onSelectField}
          getParentCount={getFieldParentCount}
          getDescendantCount={getFieldDescendantCount}
          onRevealParents={onRevealParents}
          onRevealDescendants={onRevealDescendants}
        />
      )}

      {expanded && structures.length === 0 && (
        <FieldRows
          fields={fields}
          selectedFieldId={selectedNodeId}
          qualityByNode={qualityByNode}
          controlsVisible={controlsVisible}
          onSelectField={onSelectField}
          getParentCount={getFieldParentCount}
          getDescendantCount={getFieldDescendantCount}
          onRevealParents={onRevealParents}
          onRevealDescendants={onRevealDescendants}
        />
      )}
    </div>
  );
}

function MetadataPanel({ selected, qualityByNode, onAskAgent }) {
  if (!selected) {
    return (
      <p className="muted">
        Select a rectangle to inspect the lineage entity. Click a table-like entity
        again to open its fields in-place.
      </p>
    );
  }

  const baseEntries = [
    ["id", selected.id],
    ["node_id", selected.node_id],
    ["type", selected.type],
  ];
  const propertyEntries = Object.entries(selected.properties || {}).filter(([key]) => !key.startsWith("_"));
  const entries = [...baseEntries, ...propertyEntries].filter(([, value]) => value !== undefined);
  const controls = nodeKeyCandidates(selected).flatMap((key) => qualityByNode.get(key) || []);
  return (
    <>
      <div className="node-title">
        <span className="node-type">{selected.type || "Entity"}</span>
        <strong>{entityName(selected)}</strong>
        <button className="dg-agent-link" type="button" onClick={() => onAskAgent(selected, controls)}>
          Open DQC Agent investigation
        </button>
      </div>

      {controls.length > 0 && (
        <div className="details-quality">
          {controls.map((control) => (
            <div key={control.id} className={cls("details-quality-row", controlTone(control.control_status))}>
              <strong>{control.control_status}</strong>
              <span>{control.control_name || control.quality_dimension || "Quality control"}</span>
              <code>
                {control.control_score ?? "-"}% · OK {control.ok_count ?? "-"} / {control.controlled_item_count ?? "-"} · threshold {control.acceptance_threshold ?? "-"}
              </code>
            </div>
          ))}
        </div>
      )}

      <div className="metadata">
        {entries.map(([key, value]) => (
          <div key={key} className="metadata-row">
            <span>{key}</span>
            <code>
              {value === null || value === undefined
                ? "null"
                : typeof value === "object"
                  ? JSON.stringify(value, null, 2)
                  : String(value)}
            </code>
          </div>
        ))}
      </div>
    </>
  );
}

export default function LineageExplorer() {
  const canvasRef = useRef(null);
  const panRef = useRef({
    active: false,
    startX: 0,
    startY: 0,
    scrollLeft: 0,
    scrollTop: 0,
  });
  const detailsResizeRef = useRef({
    active: false,
    startX: 0,
    startWidth: 560,
  });
  const boxDragRef = useRef({
    active: false,
    moved: false,
    nodeId: null,
    startX: 0,
    startY: 0,
    originX: 0,
    originY: 0,
  });
  const suppressClickRef = useRef({ nodeId: null, until: 0 });
  const [nodeId, setNodeId] = useState("");
  const [depth, setDepth] = useState(2);
  const [graph, setGraph] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const [expandedStructureIds, setExpandedStructureIds] = useState(() => new Set());
  const [visibleIds, setVisibleIds] = useState(() => new Set());
  const [manualPositions, setManualPositions] = useState(() => new Map());
  const [boardScale, setBoardScale] = useState(1);
  const [handMode, setHandMode] = useState(false);
  const [draggingBoxId, setDraggingBoxId] = useState(null);
  const [detailsWidth, setDetailsWidth] = useState(560);
  const [dqcControls, setDqcControls] = useState([]);
  const [unresolvedControls, setUnresolvedControls] = useState([]);
  const [activeTool, setActiveTool] = useState("Lineage");
  const [selectedTypes, setSelectedTypes] = useState(() => new Set(["Source", "Structure", "Field", "Process", "Usage"]));
  const [selectedQualities, setSelectedQualities] = useState(() => new Set(["Good", "Warning", "Critical", "Needs review"]));
  const [showOnlyIssues, setShowOnlyIssues] = useState(false);
  const [showControlsOnLineage, setShowControlsOnLineage] = useState(true);
  const [agentOpen, setAgentOpen] = useState(false);
  const [agentPrompt, setAgentPrompt] = useState("What should I fix first?");
  const [agentResponse, setAgentResponse] = useState(null);
  const [agentBusy, setAgentBusy] = useState(false);
  const [reviewBusyId, setReviewBusyId] = useState(null);
  const [userZoom, setUserZoom] = useState(1);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [error, setError] = useState("");

  const board = useMemo(
    () => graphToBoard(graph, expandedIds, visibleIds, manualPositions, expandedStructureIds),
    [graph, expandedIds, visibleIds, manualPositions, expandedStructureIds]
  );
  const qualityByNode = useMemo(() => {
    const index = new Map();
    for (const control of dqcControls) {
      const key = control.matched_node_id;
      if (!key) continue;
      const normalizedKey = String(key);
      if (!index.has(normalizedKey)) index.set(normalizedKey, []);
      index.get(normalizedKey).push(control);
    }
    return index;
  }, [dqcControls]);
  const selectedControls = useMemo(() => {
    if (!selectedNode) return [];
    return nodeKeyCandidates(selectedNode).flatMap((key) => qualityByNode.get(key) || []);
  }, [qualityByNode, selectedNode]);
  const reviewQueue = useMemo(
    () => dqcControls.filter((item) => item.human_review_required || item.confidence_level === "MEDIUM"),
    [dqcControls]
  );

  async function loadQualityControls() {
    try {
      const data = await fetchResolvedDqc(1000);
      setDqcControls(Array.isArray(data?.items) ? data.items : []);
      const unresolved = await fetchUnresolvedDqc(1000);
      setUnresolvedControls(Array.isArray(unresolved?.items) ? unresolved.items : []);
    } catch (err) {
      console.error(err);
    }
  }

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return undefined;

    const resize = () => {
      const availableWidth = Math.max(element.clientWidth - 36, 320);
      const availableHeight = Math.max(element.clientHeight - 36, 240);
      const widthScale = availableWidth / Math.max(board.width, 1);
      const heightScale = availableHeight / Math.max(board.height, 1);
      setBoardScale(Math.max(0.62, Math.min(1, widthScale, heightScale)) * userZoom);
    };

    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    return () => observer.disconnect();
  }, [board.width, board.height, userZoom]);

  useEffect(() => {
    loadQualityControls();
  }, []);

  useEffect(() => {
    function resizeDetails(event) {
      if (!detailsResizeRef.current.active) return;
      const delta = detailsResizeRef.current.startX - event.clientX;
      setDetailsWidth(Math.max(420, Math.min(900, detailsResizeRef.current.startWidth + delta)));
    }

    function stopResizeDetails() {
      detailsResizeRef.current.active = false;
      document.body.classList.remove("resizing-details");
    }

    window.addEventListener("mousemove", resizeDetails);
    window.addEventListener("mouseup", stopResizeDetails);
    return () => {
      window.removeEventListener("mousemove", resizeDetails);
      window.removeEventListener("mouseup", stopResizeDetails);
    };
  }, []);

  async function loadGraph(id = nodeId) {
    if (!id.trim()) {
      setError("Enter a node_id first.");
      return;
    }

    setLoading(true);
    setError("");
    setSelectedNode(null);
    setExpandedIds(new Set());
    setExpandedStructureIds(new Set());
    setVisibleIds(new Set());
    setManualPositions(new Map());

    try {
      const data = await fetchBusinessLineage(id.trim(), depth);
      loadQualityControls();
      const nextVisibleIds = initialVisibleIds(data);
      setGraph(data);
      const root = data.nodes?.find((node) => node.id === data.root) || data.nodes?.[0] || null;
      setSelectedNode(root);
      setVisibleIds(nextVisibleIds);
      if (root) setExpandedIds(new Set([root.id]));
    } catch (err) {
      console.error(err);
      setError("Failed to load lineage. Check the node_id and backend.");
    } finally {
      setLoading(false);
    }
  }

  async function handleSearch() {
    if (!searchText.trim()) return;

    setError("");

    try {
      const data = await searchAssets(searchText.trim(), 10);
      setSearchResults(data.results || []);
    } catch (err) {
      console.error(err);
      setError("Search failed. Check backend.");
    }
  }

  function selectSearchResult(result) {
    if (!result.node_id) return;

    setNodeId(result.node_id);
    setSearchResults([]);
    setSearchText(result.name || result.technical_name || result.node_id);

    setTimeout(() => {
      loadGraph(result.node_id);
    }, 0);
  }

  function toggleEntity(node) {
    setSelectedNode(node);
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(node.id)) next.delete(node.id);
      else next.add(node.id);
      return next;
    });
  }

  function expandAll() {
    const ids = new Set();
    board.columns.forEach((column) => column.nodes.forEach((node) => ids.add(node.id)));
    setExpandedIds(ids);
  }

  function collapseAll() {
    setExpandedIds(new Set());
    setExpandedStructureIds(new Set());
  }

  function toggleStructure(structureId) {
    setExpandedStructureIds((current) => {
      const next = new Set(current);
      if (next.has(structureId)) next.delete(structureId);
      else next.add(structureId);
      return next;
    });
  }

  function revealNeighbors(node, direction) {
    const fieldLike = isFieldNode(node);
    const adjacency = direction === "parents"
      ? fieldLike ? board.rawIncoming : board.allIncoming
      : fieldLike ? board.rawOutgoing : board.allOutgoing;
    const neighbors = adjacency.get(node.id) || new Set();
    setSelectedNode(node);
    setVisibleIds((current) => {
      const next = new Set(current);
      neighbors.forEach((id) => {
        next.add(id);
        const parentId = board.fieldParent.get(id);
        if (parentId) next.add(parentId);
      });
      return next;
    });
  }

  function hiddenNeighborCount(node, direction) {
    const fieldLike = isFieldNode(node);
    const adjacency = direction === "parents"
      ? fieldLike ? board.rawIncoming : board.allIncoming
      : fieldLike ? board.rawOutgoing : board.allOutgoing;
    const neighbors = adjacency.get(node.id) || new Set();
    return [...neighbors].filter((id) => !visibleIds.has(id)).length;
  }

  function startHandPan(event) {
    if (event.button !== 2 || !canvasRef.current) return;
    event.preventDefault();
    panRef.current = {
      active: true,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: canvasRef.current.scrollLeft,
      scrollTop: canvasRef.current.scrollTop,
    };
    setHandMode(true);
  }

  function moveHandPan(event) {
    if (boxDragRef.current.active) {
      event.preventDefault();
      const dx = (event.clientX - boxDragRef.current.startX) / Math.max(boardScale, 0.1);
      const dy = (event.clientY - boxDragRef.current.startY) / Math.max(boardScale, 0.1);
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) boxDragRef.current.moved = true;
      setManualPositions((current) => {
        const next = new Map(current);
        next.set(boxDragRef.current.nodeId, {
          x: Math.max(0, boxDragRef.current.originX + dx),
          y: Math.max(0, boxDragRef.current.originY + dy),
        });
        return next;
      });
      return;
    }

    if (!panRef.current.active || !canvasRef.current) return;
    event.preventDefault();
    const dx = event.clientX - panRef.current.startX;
    const dy = event.clientY - panRef.current.startY;
    canvasRef.current.scrollLeft = panRef.current.scrollLeft - dx;
    canvasRef.current.scrollTop = panRef.current.scrollTop - dy;
  }

  function stopHandPan() {
    if (boxDragRef.current.active) {
      if (boxDragRef.current.moved) {
        suppressClickRef.current = {
          nodeId: boxDragRef.current.nodeId,
          until: Date.now() + 250,
        };
      }
      boxDragRef.current.active = false;
      setDraggingBoxId(null);
      return;
    }
    if (!panRef.current.active) return;
    panRef.current.active = false;
    setHandMode(false);
  }

  function startBoxDrag(event, node) {
    if (event.button !== 0) return;
    const interactive = event.target.closest("button, input, textarea, select");
    if (interactive) return;
    const position = board.positions.get(node.id);
    if (!position) return;
    boxDragRef.current = {
      active: true,
      moved: false,
      nodeId: node.id,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
    };
    setDraggingBoxId(node.id);
  }

  function wasBoxDragged(node) {
    return suppressClickRef.current.nodeId === node.id && Date.now() < suppressClickRef.current.until;
  }

  function startDetailsResize(event) {
    event.preventDefault();
    detailsResizeRef.current = {
      active: true,
      startX: event.clientX,
      startWidth: detailsWidth,
    };
    document.body.classList.add("resizing-details");
  }

  function toggleSetValue(setter, value) {
    setter((current) => {
      const next = new Set(current);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  function nodeControlSummary(node) {
    const direct = nodeKeyCandidates(node).flatMap((key) => qualityByNode.get(key) || []);
    const fields = (board.fieldsByParent.get(node.id) || []).flatMap((field) =>
      nodeKeyCandidates(field).flatMap((key) => qualityByNode.get(key) || [])
    );
    return summarizeControls([...direct, ...fields]);
  }

  function nodeDimmed(node) {
    const family = assetFamily(node);
    const control = nodeControlSummary(node);
    const qFamily = qualityFamily(control);
    if (!selectedTypes.has(family)) return true;
    if (showOnlyIssues && (!control || qFamily === "Good")) return true;
    if (control && !selectedQualities.has(qFamily)) return true;
    return false;
  }

  async function runAgentInvestigation(prompt = agentPrompt) {
    setAgentOpen(true);
    setAgentBusy(true);
    setAgentResponse(null);
    try {
      const data = await askDqcAgent(prompt);
      setAgentResponse(data);
    } catch (err) {
      setAgentResponse({ error: err.message || "Agent request failed" });
    } finally {
      setAgentBusy(false);
    }
  }

  function askAgentForNode(node, controls = []) {
    const prompt = controls.length
      ? `Explain this quality issue for ${entityName(node)}. DQC control ids: ${controls.map((item) => item.id).join(", ")}`
      : `Investigate lineage asset ${entityName(node)} and explain likely quality risks.`;
    setAgentPrompt(prompt);
    runAgentInvestigation(prompt);
  }

  async function handleApprove(control) {
    setReviewBusyId(control.id);
    try {
      await approveDqcMatch(control.id, { reviewer: "demo", note: "Approved from lineage review." });
      await loadQualityControls();
    } finally {
      setReviewBusyId(null);
    }
  }

  async function handleReject(control) {
    setReviewBusyId(control.id);
    try {
      await rejectDqcMatch(control.id, { reviewer: "demo", reason: "Rejected from lineage review." });
      await loadQualityControls();
    } finally {
      setReviewBusyId(null);
    }
  }

  return (
    <div className="page lineage-page dg-lineage-shell">
      <nav className="dg-toolrail" aria-label="Lineage tools">
        {["Search", "Lineage", "Quality", "Review", "Settings"].map((item) => (
          <button
            key={item}
            className={activeTool === item ? "active" : ""}
            type="button"
            title={item}
            onClick={() => setActiveTool(item)}
          >
            {item.slice(0, 1)}
          </button>
        ))}
      </nav>

      <aside className="sidebar lineage-sidebar dg-filter-panel">
        <div className="dg-filter-brand">
          <strong>Lineage Explorer</strong>
          <span>Golden source to usage final</span>
        </div>

        <div className="sidebar-content">
          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">Asset search</h3>
              <span className="panel-badge">Catalog</span>
            </div>

            <div className="row">
              <input
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
                placeholder="Search source, table, field..."
                onKeyDown={(event) => {
                  if (event.key === "Enter") handleSearch();
                }}
              />
              <button onClick={handleSearch}>Search</button>
            </div>

            {searchResults.length > 0 && (
              <div className="results">
                {searchResults.map((result) => (
                  <button key={result.id} className="result" onClick={() => selectSearchResult(result)}>
                    <strong>{result.name || result.technical_name}</strong>
                    <span>{result.type}</span>
                    <small>{result.path || result.node_id}</small>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">Lineage root</h3>
              <span className="panel-badge">node_id</span>
            </div>

            <label>Starting entity ID</label>
            <textarea
              value={nodeId}
              onChange={(event) => setNodeId(event.target.value)}
              placeholder="Paste a Neo4j node_id here..."
              rows={4}
            />

            <label>Exploration depth</label>
            <select value={depth} onChange={(event) => setDepth(Number(event.target.value))}>
              <option value={1}>1 - Direct lineage</option>
              <option value={2}>2 - Standard lineage</option>
              <option value={3}>3 - Extended lineage</option>
              <option value={4}>4 - Large lineage</option>
              <option value={5}>5 - Very large lineage</option>
            </select>

            <button className="primary" onClick={() => loadGraph()}>
              {loading ? "Loading lineage..." : "Explore lineage"}
            </button>

            <div className="actions">
              <button onClick={expandAll}>Expand selected</button>
              <button onClick={collapseAll}>Collapse all</button>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <h3 className="panel-title">Filters</h3>
              <span className="panel-badge">{activeTool}</span>
            </div>
            <label>Asset type</label>
            <div className="dg-check-grid">
              {["Source", "Structure", "Field", "Process", "Usage"].map((item) => (
                <label key={item} className="dg-check">
                  <input
                    type="checkbox"
                    checked={selectedTypes.has(item)}
                    onChange={() => toggleSetValue(setSelectedTypes, item)}
                  />
                  <span>{item}</span>
                </label>
              ))}
            </div>

            <label>Quality</label>
            <div className="dg-check-grid">
              {["Good", "Warning", "Critical", "Needs review"].map((item) => (
                <label key={item} className="dg-check">
                  <input
                    type="checkbox"
                    checked={selectedQualities.has(item)}
                    onChange={() => toggleSetValue(setSelectedQualities, item)}
                  />
                  <span>{item}</span>
                </label>
              ))}
            </div>

            <label className="dg-toggle">
              <input type="checkbox" checked={showOnlyIssues} onChange={(event) => setShowOnlyIssues(event.target.checked)} />
              <span>Show only quality issues</span>
            </label>
            <label className="dg-toggle">
              <input type="checkbox" checked={showControlsOnLineage} onChange={(event) => setShowControlsOnLineage(event.target.checked)} />
              <span>Show controls on lineage</span>
            </label>
          </div>

          {graph && (
            <div className="panel stats">
              <div className="panel-header">
                <h3 className="panel-title">Lineage slice</h3>
              </div>
              <p>Visible entities: {visibleIds.size}</p>
              <p>Total entities: {board.nodeMap.size}</p>
              <p>Links: {board.connectors.length}</p>
              <p>Quality controls: {dqcControls.length}</p>
              <p>Unresolved: {unresolvedControls.length}</p>
              <p>Use + to reveal parents or descendants.</p>
            </div>
          )}

          {error && <div className="error">{error}</div>}
        </div>
      </aside>

      <main className="canvas-area">
        <div className="toolbar">
          <div className="toolbar-title">
            <strong>Lineage entities</strong>
            <span>
              {graph
                ? `Root entity: ${truncate(graph.root, 64)}`
                : "Search an asset or paste a node_id to start exploring"}
            </span>
          </div>

          <div className="toolbar-pills">
            <span className="pill blue">DataGalaxy-style</span>
            <span className="pill">Quality overlay</span>
            <button className="lineage-refresh-quality" type="button" onClick={loadQualityControls}>
              Refresh controls
            </button>
            <button className="lineage-refresh-quality" type="button" onClick={() => setUserZoom((value) => Math.max(0.7, value - 0.1))}>
              Zoom -
            </button>
            <button className="lineage-refresh-quality" type="button" onClick={() => setUserZoom(1)}>
              Fit
            </button>
            <button className="lineage-refresh-quality" type="button" onClick={() => setUserZoom((value) => Math.min(1.6, value + 0.1))}>
              Zoom +
            </button>
          </div>
        </div>

        <div className="graph-layout lineage-layout" style={{ gridTemplateColumns: `minmax(0, 1fr) 10px ${detailsWidth}px` }}>
          <section
            ref={canvasRef}
            className={cls("lineage-canvas", handMode && "hand-mode")}
            aria-label="Lineage entity board"
            onContextMenu={(event) => event.preventDefault()}
            onMouseDown={startHandPan}
            onMouseMove={moveHandPan}
            onMouseUp={stopHandPan}
            onMouseLeave={stopHandPan}
          >
            {!graph && (
              <div className="lineage-empty-state">
                <strong>Search or paste a lineage entity ID</strong>
                <span>The board will display connected lineage entities as expandable rectangles.</span>
              </div>
            )}

            {graph && (
              <div
                className="lineage-board-shell"
                style={{
                  width: board.width * boardScale + 520,
                  height: board.height * boardScale + 560,
                }}
              >
              <div
                className="lineage-board"
                style={{
                  width: board.width,
                  height: board.height,
                  transform: `scale(${boardScale})`,
                }}
              >
                <div className="dg-direction">
                  <strong>Golden Sources</strong>
                  <span />
                  <strong>Usage final</strong>
                </div>
                <svg className="lineage-links" width={board.width} height={board.height}>
                  <defs>
                    <marker id="lineage-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
                      <path d="M 0 0 L 10 5 L 0 10 z" />
                    </marker>
                  </defs>
                  {board.connectors.map((connector) => {
                    const mid = (connector.x1 + connector.x2) / 2;
                    const labelX = mid - 34;
                    const labelY = (connector.y1 + connector.y2) / 2 - 8;
                    return (
                      <g key={connector.id}>
                        <path
                          className="lineage-link"
                          d={`M ${connector.x1} ${connector.y1} C ${mid} ${connector.y1}, ${mid} ${connector.y2}, ${connector.x2} ${connector.y2}`}
                        />
                        <text className="lineage-link-label" x={labelX} y={labelY}>
                          {truncate(connector.label, 18)}
                        </text>
                      </g>
                    );
                  })}
                </svg>

                {board.columns.map((column, columnIndex) => (
                  <div
                    key={column.id}
                    className="lineage-column-title"
                    style={{ left: CARD_LEFT + columnIndex * (CARD_WIDTH + CARD_GAP_X), top: 24 }}
                  >
                    {column.title}
                  </div>
                ))}

                {board.columns.flatMap((column) =>
                  column.nodes.map((node) => (
                    <EntityCard
                      key={node.id}
                      node={node}
                      fields={board.fieldsByParent.get(node.id) || []}
                      structures={board.structuresBySource.get(node.id) || []}
                      fieldsByParent={board.fieldsByParent}
                      expandedStructureIds={expandedStructureIds}
                      qualityByNode={qualityByNode}
                      expanded={expandedIds.has(node.id)}
                      selected={selectedNode?.id === node.id || draggingBoxId === node.id}
                      selectedNodeId={selectedNode?.id}
                      root={node.id === board.rootId}
                      position={board.positions.get(node.id)}
                      parentCount={hiddenNeighborCount(node, "parents")}
                      descendantCount={hiddenNeighborCount(node, "descendants")}
                      onToggle={toggleEntity}
                      onSelectField={setSelectedNode}
                      onToggleStructure={toggleStructure}
                      getFieldParentCount={(field) => hiddenNeighborCount(field, "parents")}
                      getFieldDescendantCount={(field) => hiddenNeighborCount(field, "descendants")}
                      onRevealParents={(item) => revealNeighbors(item, "parents")}
                      onRevealDescendants={(item) => revealNeighbors(item, "descendants")}
                      onDragStart={startBoxDrag}
                      wasDragged={wasBoxDragged}
                      dimmed={nodeDimmed(node)}
                      controlsVisible={showControlsOnLineage}
                    />
                  ))
                )}
              </div>
              </div>
            )}
          </section>

          <button
            className="details-resizer"
            type="button"
            aria-label="Resize entity details panel"
            onMouseDown={startDetailsResize}
          />

          <section className="details">
            <div className="details-header">
              <h2>Entity details</h2>
              {selectedNode && <span>{Object.keys(selectedNode.properties || {}).length + 3} metadata fields</span>}
            </div>

            <div className="details-body">
              <MetadataPanel selected={selectedNode} qualityByNode={qualityByNode} onAskAgent={askAgentForNode} />
            </div>
          </section>
        </div>

        <section className="dg-control-panel">
          <div className="dg-control-head">
            <div>
              <strong>DQC controls for selected asset</strong>
              <span>{selectedNode ? entityName(selectedNode) : "Select an asset to inspect controls"}</span>
            </div>
            <button type="button" onClick={() => runAgentInvestigation()}>
              Ask Agent
            </button>
          </div>
          <div className="dg-control-table">
            {selectedControls.length === 0 && <p className="muted">No resolved controls attached to this asset yet.</p>}
            {selectedControls.map((control) => (
              <div key={control.id} className={cls("dg-control-row", controlTone(control.control_status))}>
                <span>{control.quality_dimension || "Quality"}</span>
                <strong>{control.control_status}</strong>
                <code>{formatPercent(control.control_score)} · OK {control.ok_count ?? "-"} / {control.controlled_item_count ?? "-"}</code>
                <small>{control.match_method} · {control.confidence_level}</small>
              </div>
            ))}
          </div>

          {reviewQueue.length > 0 && (
            <div className="dg-review-strip">
              <strong>Human Review Queue</strong>
              {reviewQueue.slice(0, 3).map((control) => (
                <div key={control.id} className="dg-review-card">
                  <span>{truncate(control.matched_path_full || control.control_name || "DQC match", 42)}</span>
                  <small>{control.match_method} · score {control.match_score}</small>
                  <button disabled={reviewBusyId === control.id} onClick={() => handleApprove(control)}>Approve</button>
                  <button disabled={reviewBusyId === control.id} onClick={() => handleReject(control)}>Reject</button>
                  <button onClick={() => askAgentForNode(selectedNode || { label: control.matched_path_full }, [control])}>Ask Agent</button>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>

      {agentOpen && (
        <aside className="dg-agent-drawer">
          <div className="dg-agent-head">
            <strong>Agent Investigation Panel</strong>
            <button type="button" onClick={() => setAgentOpen(false)}>Close</button>
          </div>
          <textarea value={agentPrompt} onChange={(event) => setAgentPrompt(event.target.value)} rows={5} />
          <button className="primary" type="button" onClick={() => runAgentInvestigation()} disabled={agentBusy}>
            {agentBusy ? "Investigating..." : "Run investigation"}
          </button>
          <div className="dg-agent-card">
            {!agentResponse && <p className="muted">Ask about unresolved events, medium-confidence matches, or what to fix first.</p>}
            {agentResponse && (
              <>
                <h3>Summary</h3>
                <pre>{JSON.stringify(agentResponse, null, 2)}</pre>
              </>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}
