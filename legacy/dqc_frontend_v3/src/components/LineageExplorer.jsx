import React, { useEffect, useMemo, useRef, useState } from "react";

import {
  askDqcAgent,
  fetchBusinessLineage,
  fetchResolvedDqc,
  fetchUnresolvedDqc,
  searchAssets,
} from "../api";
import {
  assetIcon,
  classifyAssetType,
  getNodeName,
  getNodePath,
  normalizeText,
} from "../lineageUtils";

const STAGE_LABELS = ["Golden Sources", "Transformations", "Datasets", "Usage final"];
const STAGE_WIDTH = 340;
const CARD_WIDTH = 292;
const CARD_Y_GAP = 34;
const BOARD_TOP = 112;
const BOARD_LEFT = 48;
const CARD_COLLISION_GAP = 18;

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
  if (items.some((item) => koRate(item) > 0 || item.control_status === "FAILED")) {
    return { tone: "critical", label: "Issue" };
  }
  if (items.some((item) => item.confidence_level === "HIGH")) return { tone: "good", label: "Validated" };
  return { tone: "neutral", label: "DQC" };
}

function getNodeStage(node) {
  const family = classifyAssetType(node?.type);
  if (family === "source") return 0;
  if (family === "process") return 1;
  if (family === "usage") return 3;
  return 2;
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

function buildCards(graph, qualityByKey) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const sources = nodes.filter((node) => classifyAssetType(node.type) === "source");
  const structures = nodes.filter((node) => classifyAssetType(node.type) === "structure");
  const fields = nodes.filter((node) => classifyAssetType(node.type) === "field");
  const structureParent = new Map();
  const fieldParent = new Map();

  edges.forEach((edge) => {
    const source = nodeMap.get(edge.source);
    const target = nodeMap.get(edge.target);
    if (!source || !target) return;
    const sourceType = classifyAssetType(source.type);
    const targetType = classifyAssetType(target.type);
    if (sourceType === "source" && targetType === "structure") structureParent.set(target.id, source.id);
    if (targetType === "source" && sourceType === "structure") structureParent.set(source.id, target.id);
    if (sourceType === "structure" && targetType === "field") fieldParent.set(target.id, source.id);
    if (targetType === "structure" && sourceType === "field") fieldParent.set(source.id, target.id);
  });

  structures.forEach((structure) => {
    if (structureParent.has(structure.id)) return;
    const inferred = inferSourceForStructure(structure, sources);
    if (inferred) structureParent.set(structure.id, inferred.id);
  });

  const childrenBySource = new Map();
  const fieldsByStructure = new Map();
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

  const nodeToCard = new Map();
  const cards = [];
  sources.forEach((source) => {
    const card = {
      id: `source:${source.id}`,
      node: source,
      stage: 0,
      kind: "source",
      structures: childrenBySource.get(source.id) || [],
      fieldsByStructure,
    };
    cards.push(card);
    nodeToCard.set(source.id, card.id);
    card.structures.forEach((structure) => {
      nodeToCard.set(structure.id, card.id);
      (fieldsByStructure.get(structure.id) || []).forEach((field) => nodeToCard.set(field.id, card.id));
    });
  });

  nodes.forEach((node) => {
    if (nodeToCard.has(node.id)) return;
    if (classifyAssetType(node.type) === "field") return;
    const stage = getNodeStage(node);
    const card = {
      id: `node:${node.id}`,
      node,
      stage,
      kind: classifyAssetType(node.type),
      structures: [],
      fieldsByStructure,
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
    const key = `${sourceCard}->${targetCard}`;
    if (seen.has(key)) return;
    seen.add(key);
    const sourceQuality = cardsById.get(sourceCard)?.badge;
    const targetQuality = cardsById.get(targetCard)?.badge;
    links.push({
      id: `${key}-${index}`,
      source: sourceCard,
      target: targetCard,
      type: edge.type || "lineage",
      warning: sourceQuality?.tone === "critical" || targetQuality?.tone === "critical",
    });
  });

  return { cards, links, nodeMap, nodeToCard };
}

function collectQualityForCard(card, qualityByKey) {
  const nodes = [card.node, ...card.structures];
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
  nodeKeys(node).forEach((key) => {
    (qualityByKey.get(key) || []).forEach((item) => {
      const id = item.id || JSON.stringify(item);
      if (!seen.has(id)) {
        seen.add(id);
        items.push(item);
      }
    });
  });
  return items;
}

function getExpandedCardHeight(card, expanded) {
  if (!expanded?.structures) return 104;

  const openFields = expanded?.fieldIds?.size
    ? card.structures.reduce(
        (sum, structure) =>
          sum + (expanded.fieldIds.has(structure.id) ? card.fieldsByStructure.get(structure.id)?.length || 0 : 0),
        0
      )
    : 0;

  const structureRows = Math.max(card.structures.length, 1);
  return 116 + structureRows * 44 + openFields * 34;
}

function resolveVerticalSpace(columns, positions) {
  columns.forEach((column) => {
    const ordered = [...column.cards].sort((left, right) => {
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

function enrichLayout(cards, links, expandedCards, filters, manualPositions) {
  const filtered = cards.filter((card) => {
    const text = normalizeText(`${getNodeName(card.node)} ${card.node?.node_id} ${getNodePath(card.node)}`);
    const matchSearch = !filters.canvasSearch || text.includes(normalizeText(filters.canvasSearch));
    const matchType = filters.assetType === "all" || card.kind === filters.assetType;
    const matchIssue = !filters.issuesOnly || ["critical", "review"].includes(card.badge?.tone);
    return matchSearch && matchType && matchIssue;
  });
  const visibleIds = new Set(filtered.map((card) => card.id));
  const columns = STAGE_LABELS.map((label, stage) => ({
    label,
    stage,
    cards: filtered.filter((card) => card.stage === stage),
  }));
  const positions = new Map();
  columns.forEach((column, columnIndex) => {
    let y = BOARD_TOP;
    column.cards.forEach((card) => {
      const expanded = expandedCards[card.id];
      const height = getExpandedCardHeight(card, expanded);
      const manual = manualPositions.get(card.id);
      positions.set(card.id, {
        x: manual?.x ?? BOARD_LEFT + columnIndex * STAGE_WIDTH,
        y: manual?.y ?? y,
        height,
      });
      y += height + CARD_Y_GAP;
    });
  });
  resolveVerticalSpace(columns, positions);

  const visibleLinks = links.filter((link) => visibleIds.has(link.source) && visibleIds.has(link.target));
  const boardHeight = Math.max(
    520,
    ...[...positions.values()].map((position) => position.y + position.height + 120)
  );
  const boardWidth = Math.max(
    BOARD_LEFT * 2 + STAGE_WIDTH * STAGE_LABELS.length,
    ...[...positions.values()].map((position) => position.x + CARD_WIDTH + 160)
  );
  return { columns, positions, links: visibleLinks, boardHeight, boardWidth };
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

function SourceCard({ card, expanded, selectedId, showBadges, onSelect, onToggleSource, onToggleStructure }) {
  return (
    <button
      className={cls("dg-card", "source", selectedId === card.id && "selected", card.badge?.tone)}
      type="button"
      onClick={() => {
        onSelect({ type: "card", card, node: card.node });
        onToggleSource(card.id);
      }}
    >
      <CardHeader card={card} showBadges={showBadges} />
      {expanded?.structures && (
        <div className="dg-card-children">
          {card.structures.length === 0 && <div className="dg-empty-inline">No structures returned</div>}
          {card.structures.map((structure) => {
            const structureOpen = expanded.fieldIds?.has(structure.id);
            return (
              <div key={structure.id} className="dg-structure-block">
                <button
                  type="button"
                  className={cls("dg-child-row", structureOpen && "open")}
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
                {structureOpen && (
                  <div className="dg-field-list">
                    {(card.fieldsByStructure.get(structure.id) || []).map((field) => (
                      <button
                        key={field.id}
                        type="button"
                        className="dg-field-row"
                        onClick={(event) => {
                          event.stopPropagation();
                          onSelect({ type: "field", card, node: field });
                        }}
                      >
                        <span>FLD</span>
                        {compact(getNodeName(field), 28)}
                      </button>
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
    </button>
  );
}

function CardHeader({ card, showBadges }) {
  return (
    <div className="dg-card-head">
      <span className={cls("dg-asset-icon", card.kind)}>{assetIcon(card.node?.type)}</span>
      <span className="dg-card-copy">
        <small>{compact(getNodePath(card.node), 36) || card.kind}</small>
        <strong>{compact(getNodeName(card.node), 28)}</strong>
        <em>{card.node?.node_id || card.node?.id}</em>
      </span>
      <QualityBadge badge={card.badge} show={showBadges} />
    </div>
  );
}

function AssetCard({ card, selectedId, showBadges, onSelect }) {
  return (
    <button
      className={cls("dg-card", card.kind, selectedId === card.id && "selected", card.badge?.tone)}
      type="button"
      onClick={() => onSelect({ type: "card", card, node: card.node })}
    >
      <CardHeader card={card} showBadges={showBadges} />
    </button>
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
            className={cls("dg-link", link.warning && "warning")}
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
        <div key={item.id || index} className={cls("dg-control-card", badgeFromDqc([item])?.tone)}>
          <strong>{item.control_name || item.quality_dimension || item.failure_reason || "Quality control"}</strong>
          <div>
            <span>Dimension</span>
            <code>{item.quality_dimension || "-"}</code>
          </div>
          <div>
            <span>Counts</span>
            <code>
              OK {item.ok_count ?? "-"} / KO {item.ko_count ?? "-"} / Total {item.controlled_item_count ?? "-"}
            </code>
          </div>
          <div>
            <span>KO rate</span>
            <code>{Math.round(koRate(item) * 1000) / 10}%</code>
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
  const [depth, setDepth] = useState(2);
  const [assetType, setAssetType] = useState("all");
  const [issuesOnly, setIssuesOnly] = useState(false);
  const [showBadges, setShowBadges] = useState(true);
  const [searchText, setSearchText] = useState("");
  const [canvasSearch, setCanvasSearch] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [graph, setGraph] = useState(null);
  const [resolvedDqc, setResolvedDqc] = useState([]);
  const [unresolvedDqc, setUnresolvedDqc] = useState([]);
  const [expandedCards, setExpandedCards] = useState({});
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
      enrichLayout(board.cards, board.links, expandedCards, {
        assetType,
        canvasSearch,
        issuesOnly,
      }, manualPositions),
    [board.cards, board.links, expandedCards, assetType, canvasSearch, issuesOnly, manualPositions]
  );

  const selectedQuality = useMemo(
    () => (selected?.node ? collectQualityForNode(selected.node, qualityByKey) : []),
    [selected, qualityByKey]
  );

  const lineageSummary = useMemo(() => {
    if (!selected?.card) return { upstream: 0, downstream: 0 };
    return {
      upstream: board.links.filter((link) => link.target === selected.card.id).length,
      downstream: board.links.filter((link) => link.source === selected.card.id).length,
    };
  }, [selected, board.links]);

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

  async function loadLineage(id = nodeId) {
    if (!id.trim()) {
      setError("Enter a node_id or choose a search result first.");
      return;
    }
    setLoading(true);
    setError("");
    setGraph(null);
    setSelected(null);
    setExpandedCards({});
    setManualPositions(new Map());
    setAgentState({ loading: false, answer: null, error: "" });
    try {
      const data = await fetchBusinessLineage(id.trim(), depth);
      setGraph({
        root: data?.root,
        nodes: Array.isArray(data?.nodes) ? data.nodes : [],
        edges: Array.isArray(data?.edges) ? data.edges : [],
      });
      await loadDqc();
    } catch (err) {
      setError(err.message || "Failed to load lineage. Check the node_id and backend.");
    } finally {
      setLoading(false);
    }
  }

  function selectSearchResult(result) {
    const id = result.node_id || result.id;
    if (!id) return;
    setNodeId(id);
    setSearchText(result.name || result.technical_name || result.label || id);
    setSearchResults([]);
    loadLineage(id);
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

        <label>Depth</label>
        <select value={depth} onChange={(event) => setDepth(Number(event.target.value))}>
          <option value={1}>1 - Direct</option>
          <option value={2}>2 - Standard</option>
          <option value={3}>3 - Extended</option>
          <option value={4}>4 - Large</option>
          <option value={5}>5 - Very large</option>
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

        <div className="dg-side-stats">
          <span>Cards <strong>{layout.columns.reduce((sum, col) => sum + col.cards.length, 0)}</strong></span>
          <span>Links <strong>{layout.links.length}</strong></span>
          <span>DQC <strong>{resolvedDqc.length + unresolvedDqc.length}</strong></span>
        </div>
        {qualityLoading && <p className="dg-muted">Refreshing DQC overlays...</p>}
        {error && <div className="dg-error">{error}</div>}
      </aside>

      <main className="dg-lineage-main" ref={workspaceRef}>
        <header className="dg-canvas-toolbar">
          <div>
            <strong>End-to-end lineage</strong>
            <span>Golden Sources to Usage final, with source/structure/field drill-down folded into asset cards.</span>
          </div>
          <div className="dg-zoom-controls">
            <button type="button" onClick={() => setZoom((value) => Math.max(0.65, value - 0.1))}>-</button>
            <button type="button" onClick={fitBoard}>Fit</button>
            <button type="button" onClick={() => setZoom((value) => Math.min(1.25, value + 0.1))}>+</button>
            <button type="button" onClick={toggleFullscreen}>{isFullscreen ? "Exit" : "Full"}</button>
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
              <span>The default view will render a left-to-right chain from Golden Sources to Usage final.</span>
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

          {graph && (
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
              {layout.columns.map((column, index) => (
                <div
                  key={column.label}
                  className="dg-stage-title"
                  style={{ left: BOARD_LEFT + index * STAGE_WIDTH, top: 64 }}
                >
                  {column.label}
                </div>
              ))}
              {layout.columns.flatMap((column) =>
                column.cards.map((card) => {
                  const position = layout.positions.get(card.id);
                  const style = { left: position.x, top: position.y, width: CARD_WIDTH };
                  if (card.kind === "source") {
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
                          showBadges={showBadges}
                          onSelect={setSelected}
                          onToggleSource={toggleSource}
                          onToggleStructure={toggleStructure}
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
                      <AssetCard card={card} selectedId={selectedId} showBadges={showBadges} onSelect={setSelected} />
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
