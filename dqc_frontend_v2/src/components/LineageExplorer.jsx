import React, { useEffect, useMemo, useRef, useState } from "react";

import { fetchBusinessLineage, searchAssets } from "../api";

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

function isLineageNode(node) {
  const type = normalizeType(node?.type);
  if (type.includes("term") || type.includes("glossary")) return false;
  return LINEAGE_TYPES.has(type) || [...LINEAGE_TYPES].some((item) => type.includes(item));
}

function isFieldNode(node) {
  const type = normalizeType(node?.type);
  return FIELD_TYPES.has(type) || type.includes("field") || type.includes("processingitem") || type.includes("processing item");
}

function edgeLabel(edge) {
  const raw = String(edge?.type || edge?.properties?.link_type || "lineage");
  return raw.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
}

function getCardHeight(node, fieldsByParent, expandedIds) {
  const fields = fieldsByParent.get(node.id) || [];
  if (!expandedIds.has(node.id)) return CARD_HEIGHT;
  return CARD_HEIGHT + 28 + Math.max(fields.length, 1) * FIELD_ROW_HEIGHT;
}

function graphToBoard(graph, expandedIds, visibleIds, manualPositions) {
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

  for (const edge of lineageEdges) {
    const source = nodeMap.get(edge.source);
    const target = nodeMap.get(edge.target);
    if (source && target && isFieldNode(source) && !isFieldNode(target)) fieldParent.set(source.id, target.id);
    if (source && target && !isFieldNode(source) && isFieldNode(target)) fieldParent.set(target.id, source.id);
  }

  for (const node of lineageNodes.filter(isFieldNode)) {
    const parentId = fieldParent.get(node.id);
    if (!parentId || !cardIdSet.has(parentId)) continue;
    if (!fieldsByParent.has(parentId)) fieldsByParent.set(parentId, []);
    fieldsByParent.get(parentId).push(node);
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
      const height = getCardHeight(node, fieldsByParent, expandedIds);
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
      {fields.map((field) => (
        <button
          key={field.id}
          className={cls("lineage-field-row", selectedFieldId === field.id && "active")}
          onClick={(event) => {
            event.stopPropagation();
            onSelectField(field);
          }}
          type="button"
        >
          <span className="field-status" />
          <span>{truncate(entityName(field), 30)}</span>
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
      ))}
    </div>
  );
}

function EntityCard({
  node,
  fields,
  expanded,
  selected,
  selectedNodeId,
  root,
  position,
  parentCount,
  descendantCount,
  onToggle,
  onSelectField,
  getFieldParentCount,
  getFieldDescendantCount,
  onRevealParents,
  onRevealDescendants,
  onDragStart,
  wasDragged,
}) {
  return (
    <div
      className={cls("lineage-card", cardTone(node.type, root), selected && "selected", expanded && "expanded")}
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
        <span className="lineage-icon">{iconLabel(node.type)}</span>
        <span className="lineage-copy">
          <small>{truncate(entitySubtitle(node), 32)}</small>
          <strong>{truncate(entityName(node), 24)}</strong>
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
          {fields.length > 0 && <span className="lineage-field-count">{fields.length}</span>}
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

      {expanded && (
        <FieldRows
          fields={fields}
          selectedFieldId={selectedNodeId}
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

function MetadataPanel({ selected }) {
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
  return (
    <>
      <div className="node-title">
        <span className="node-type">{selected.type || "Entity"}</span>
        <strong>{entityName(selected)}</strong>
      </div>

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
  const [visibleIds, setVisibleIds] = useState(() => new Set());
  const [manualPositions, setManualPositions] = useState(() => new Map());
  const [boardScale, setBoardScale] = useState(1);
  const [handMode, setHandMode] = useState(false);
  const [draggingBoxId, setDraggingBoxId] = useState(null);
  const [detailsWidth, setDetailsWidth] = useState(560);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [error, setError] = useState("");

  const board = useMemo(
    () => graphToBoard(graph, expandedIds, visibleIds, manualPositions),
    [graph, expandedIds, visibleIds, manualPositions]
  );

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return undefined;

    const resize = () => {
      const availableWidth = Math.max(element.clientWidth - 36, 320);
      const availableHeight = Math.max(element.clientHeight - 36, 240);
      const widthScale = availableWidth / Math.max(board.width, 1);
      const heightScale = availableHeight / Math.max(board.height, 1);
      setBoardScale(Math.max(0.62, Math.min(1, widthScale, heightScale)));
    };

    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    return () => observer.disconnect();
  }, [board.width, board.height]);

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
    setVisibleIds(new Set());
    setManualPositions(new Map());

    try {
      const data = await fetchBusinessLineage(id.trim(), depth);
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

  return (
    <div className="page lineage-page">
      <aside className="sidebar lineage-sidebar">
        <div className="brand">
          <div className="brand-mark">DG</div>
          <div>
            <h1>DataGalaxy Lineage</h1>
            <p>Tabular lineage exploration</p>
          </div>
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
              <button onClick={expandAll}>Show fields</button>
              <button onClick={collapseAll}>Hide fields</button>
            </div>
          </div>

          {graph && (
            <div className="panel stats">
              <div className="panel-header">
                <h3 className="panel-title">Lineage slice</h3>
              </div>
              <p>Visible entities: {visibleIds.size}</p>
              <p>Total entities: {board.nodeMap.size}</p>
              <p>Links: {board.connectors.length}</p>
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
            <span className="pill">Lineage only</span>
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
                      expanded={expandedIds.has(node.id)}
                      selected={selectedNode?.id === node.id || draggingBoxId === node.id}
                      selectedNodeId={selectedNode?.id}
                      root={node.id === board.rootId}
                      position={board.positions.get(node.id)}
                      parentCount={hiddenNeighborCount(node, "parents")}
                      descendantCount={hiddenNeighborCount(node, "descendants")}
                      onToggle={toggleEntity}
                      onSelectField={setSelectedNode}
                      getFieldParentCount={(field) => hiddenNeighborCount(field, "parents")}
                      getFieldDescendantCount={(field) => hiddenNeighborCount(field, "descendants")}
                      onRevealParents={(item) => revealNeighbors(item, "parents")}
                      onRevealDescendants={(item) => revealNeighbors(item, "descendants")}
                      onDragStart={startBoxDrag}
                      wasDragged={wasBoxDragged}
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
              <MetadataPanel selected={selectedNode} />
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
