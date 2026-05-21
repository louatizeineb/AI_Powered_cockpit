import React, { useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";

import { fetchBusinessLineage, searchAssets } from "../api";
import {
  createDatagalaxyStylesheet,
  graphToDatagalaxyElements,
  getEntityIcon,
  getQualityStatus,
} from "../lineageUtils";

cytoscape.use(dagre);

function shortText(value, max = 42) {
  const text = String(value || "").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

function prettyType(type) {
  const t = String(type || "Asset");
  if (t.toLowerCase().includes("dataprocessing")) return "Process";
  if (t.toLowerCase().includes("business")) return "Business term";
  return t;
}

function pathFromNode(node) {
  const p = node?.properties || {};
  return p.path_full || p.path || p.full_path || p.qualified_name || node?.node_id || node?.id || "";
}

function qualityText(node) {
  const status = getQualityStatus(node || {});
  if (status.level === "unknown") return "No DQC signal";
  if (status.level === "good") return "Quality validated";
  if (status.level === "warning") return "Data quality warning";
  return "Data quality issue";
}

export default function LineageExplorer() {
  const cyContainerRef = useRef(null);
  const cyRef = useRef(null);
  const lastTapRef = useRef({ time: 0, nodeId: null });

  const [nodeId, setNodeId] = useState("");
  const [depth, setDepth] = useState(2);
  const [graph, setGraph] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [hoverPosition, setHoverPosition] = useState({ x: 0, y: 0 });
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [error, setError] = useState("");
  const [layoutMode, setLayoutMode] = useState("LR");
  const [showOnlyIssues, setShowOnlyIssues] = useState(false);

  const graphStats = useMemo(() => {
    if (!graph) return { nodes: 0, edges: 0, fields: 0, qualityIssues: 0 };
    const nodes = graph.nodes || [];
    return {
      nodes: graph.stats?.node_count ?? nodes.length,
      edges: graph.stats?.edge_count ?? (graph.edges || []).length,
      fields: nodes.filter((n) => String(n.type || "").toLowerCase().includes("field")).length,
      qualityIssues: nodes.filter((n) => {
        const level = getQualityStatus(n).level;
        return level === "warning" || level === "bad";
      }).length,
    };
  }, [graph]);

  async function loadGraph(id = nodeId) {
    if (!id.trim()) {
      setError("Enter a node_id first, or search an asset and select it.");
      return;
    }

    setLoading(true);
    setError("");
    setSelectedNode(null);
    setHoveredNode(null);

    try {
      const data = await fetchBusinessLineage(id.trim(), depth);
      setGraph(data);
      renderGraph(data, layoutMode, showOnlyIssues);
    } catch (err) {
      console.error(err);
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          "Failed to load lineage. Check the node_id and backend."
      );
    } finally {
      setLoading(false);
    }
  }

  function makeLayout(rankDir = layoutMode) {
    return {
      name: "dagre",
      rankDir,
      nodeSep: 46,
      rankSep: 170,
      edgeSep: 18,
      padding: 36,
      animate: true,
      animationDuration: 450,
    };
  }

  function renderGraph(data, rankDir = layoutMode, onlyIssues = showOnlyIssues) {
    if (!cyContainerRef.current) return;

    const elements = graphToDatagalaxyElements(data, { onlyIssues });

    if (cyRef.current) {
      cyRef.current.destroy();
    }

    const cy = cytoscape({
      container: cyContainerRef.current,
      elements,
      style: createDatagalaxyStylesheet(data.root),
      layout: makeLayout(rankDir),
      minZoom: 0.12,
      maxZoom: 2.4,
      wheelSensitivity: 0.12,
      textureOnViewport: true,
      hideEdgesOnViewport: false,
      motionBlur: true,
      motionBlurOpacity: 0.15,
    });

    cy.on("tap", "node", (event) => {
      const node = event.target;
      setSelectedNode(node.data());

      const now = Date.now();
      const previous = lastTapRef.current;
      if (previous.nodeId === node.id() && now - previous.time < 360) {
        toggleCollapse(node);
      }
      lastTapRef.current = { time: now, nodeId: node.id() };
    });

    cy.on("mouseover", "node", (event) => {
      const rendered = event.renderedPosition || { x: 0, y: 0 };
      setHoveredNode(event.target.data());
      setHoverPosition({ x: rendered.x + 26, y: rendered.y + 20 });
    });

    cy.on("mouseout", "node", () => setHoveredNode(null));

    cy.on("tap", (event) => {
      if (event.target === cy) setSelectedNode(null);
    });

    cyRef.current = cy;
    setTimeout(() => cy.fit(undefined, 70), 120);
  }

  function rerenderCurrent(nextLayout = layoutMode, onlyIssues = showOnlyIssues) {
    if (!graph) return;
    renderGraph(graph, nextLayout, onlyIssues);
  }

  function toggleCollapse(node) {
    const cy = cyRef.current;
    if (!cy) return;

    const isCollapsed = node.hasClass("collapsed");
    const connectedEdges = node.connectedEdges();
    const neighborNodes = connectedEdges.connectedNodes().difference(node);

    if (isCollapsed) {
      neighborNodes.removeClass("hidden");
      connectedEdges.removeClass("hidden");
      node.removeClass("collapsed");
      node.data("collapsed", false);
    } else {
      neighborNodes.addClass("hidden");
      connectedEdges.addClass("hidden");
      node.addClass("collapsed");
      node.data("collapsed", true);
    }

    cy.layout(makeLayout(layoutMode)).run();
  }

  function resetCollapse() {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("hidden");
    cy.nodes().removeClass("collapsed");
    cy.layout(makeLayout(layoutMode)).run();
    cy.fit(undefined, 70);
  }

  function fitGraph() {
    const cy = cyRef.current;
    if (!cy) return;
    cy.fit(undefined, 70);
  }

  function changeLayout(next) {
    setLayoutMode(next);
    if (cyRef.current) {
      cyRef.current.layout(makeLayout(next)).run();
      setTimeout(() => cyRef.current?.fit(undefined, 70), 250);
    }
  }

  function toggleQualityFilter() {
    const next = !showOnlyIssues;
    setShowOnlyIssues(next);
    rerenderCurrent(layoutMode, next);
  }

  async function handleSearch() {
    if (!searchText.trim()) return;
    setError("");
    try {
      const data = await searchAssets(searchText.trim(), 12);
      setSearchResults(data.results || data.items || []);
    } catch (err) {
      console.error(err);
      setError(err?.message || "Search failed. Check backend/CORS.");
    }
  }

  function selectSearchResult(result) {
    const nextNodeId = result.node_id || result.id;
    if (!nextNodeId) return;
    setNodeId(nextNodeId);
    setSearchResults([]);
    setSearchText(result.name || result.technical_name || result.label || nextNodeId);
    setTimeout(() => loadGraph(nextNodeId), 0);
  }

  useEffect(() => {
    return () => {
      if (cyRef.current) cyRef.current.destroy();
    };
  }, []);

  return (
    <div className="dg-lineage-shell">
      <aside className="dg-left-rail" aria-label="Lineage tools">
        <button className="rail-btn active" title="Lineage graph">⌘</button>
        <button className="rail-btn" title="Quality overlay">◐</button>
        <button className="rail-btn" title="Insights">◌</button>
        <button className="rail-btn" title="Save view">▣</button>
      </aside>

      <aside className="dg-sidebar">
        <div className="dg-brand-card">
          <div className="dg-brand-icon">DG</div>
          <div>
            <h1>Lineage Explorer</h1>
            <p>DataGalaxy-style catalog graph</p>
          </div>
        </div>

        <div className="dg-panel dg-search-panel">
          <div className="dg-panel-kicker">Search & open</div>
          <h2>Find a catalog asset</h2>
          <div className="dg-search-box">
            <span>⌕</span>
            <input
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search table, field, source…"
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            />
            <button onClick={handleSearch}>Search</button>
          </div>

          {searchResults.length > 0 && (
            <div className="dg-result-stack">
              {searchResults.map((result) => (
                <button
                  key={result.id || result.node_id}
                  className="dg-result-card"
                  onClick={() => selectSearchResult(result)}
                >
                  <span className="result-icon">{getEntityIcon(result.type)}</span>
                  <span className="result-main">
                    <strong>{shortText(result.name || result.technical_name || result.label || result.node_id, 34)}</strong>
                    <small>{shortText(result.path || result.path_full || result.node_id, 52)}</small>
                  </span>
                  <em>{prettyType(result.type)}</em>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="dg-panel">
          <div className="dg-panel-kicker">Root entity</div>
          <h2>Launch lineage</h2>
          <label>Neo4j node_id</label>
          <textarea
            value={nodeId}
            onChange={(e) => setNodeId(e.target.value)}
            placeholder="Paste node_id, or select a search result…"
            rows={3}
          />

          <div className="dg-inline-fields">
            <div>
              <label>Depth</label>
              <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
                <option value={1}>1</option>
                <option value={2}>2</option>
                <option value={3}>3</option>
                <option value={4}>4</option>
                <option value={5}>5</option>
              </select>
            </div>
            <div>
              <label>Layout</label>
              <select value={layoutMode} onChange={(e) => changeLayout(e.target.value)}>
                <option value="LR">Left → Right</option>
                <option value="TB">Top → Bottom</option>
              </select>
            </div>
          </div>

          <button className="dg-primary" onClick={() => loadGraph()} disabled={loading}>
            {loading ? "Building graph…" : "Explore lineage"}
          </button>

          <div className="dg-action-row">
            <button onClick={fitGraph}>Fit</button>
            <button onClick={resetCollapse}>Expand</button>
            <button className={showOnlyIssues ? "active-filter" : ""} onClick={toggleQualityFilter}>
              Quality only
            </button>
          </div>
        </div>

        {graph && (
          <div className="dg-stats-grid">
            <div><strong>{graphStats.nodes}</strong><span>nodes</span></div>
            <div><strong>{graphStats.edges}</strong><span>edges</span></div>
            <div><strong>{graphStats.fields}</strong><span>fields</span></div>
            <div><strong>{graphStats.qualityIssues}</strong><span>DQ alerts</span></div>
          </div>
        )}

        <div className="dg-panel dg-legend-panel">
          <div className="dg-panel-kicker">Legend</div>
          <div className="dg-legend-grid">
            {[
              ["source", "Source"],
              ["container", "Container"],
              ["structure", "Structure"],
              ["field", "Field"],
              ["process", "Process"],
              ["term", "Business term"],
            ].map(([cls, label]) => (
              <span key={cls} className="dg-legend-item"><i className={cls} />{label}</span>
            ))}
          </div>
        </div>

        {error && <div className="dg-error">{error}</div>}
      </aside>

      <main className="dg-canvas-area">
        <header className="dg-toolbar">
          <div>
            <span className="dg-breadcrumb">DataSets › Client Data › Lineage</span>
            <h2>{graph ? "Interactive lineage map" : "Choose an asset to visualize lineage"}</h2>
          </div>
          <div className="dg-toolbar-actions">
            <span className="dg-status-pill good">● Quality overlay</span>
            <span className="dg-status-pill">Neo4j</span>
            <span className="dg-status-pill">Depth {depth}</span>
          </div>
        </header>

        <div className="dg-graph-stage">
          {!graph && (
            <div className="dg-empty-state">
              <div className="empty-orbit">⌕</div>
              <h3>Search, select, and explore lineage</h3>
              <p>Open a source, structure, or field to display a DataGalaxy-like lineage map with quality signals.</p>
            </div>
          )}

          <div ref={cyContainerRef} className="dg-cy-container" />

          {hoveredNode && (
            <div className="dg-quality-tooltip" style={{ left: hoverPosition.x, top: hoverPosition.y }}>
              <strong>{qualityText(hoveredNode)}</strong>
              <span>{shortText(hoveredNode.label, 32)}</span>
              <small>{shortText(pathFromNode(hoveredNode), 52)}</small>
            </div>
          )}
        </div>
      </main>

      <aside className="dg-details-panel">
        <div className="dg-details-header">
          <span>Entity details</span>
          {selectedNode && <button onClick={() => setNodeId(selectedNode.node_id || selectedNode.id)}>Use as root</button>}
        </div>

        {!selectedNode ? (
          <div className="dg-details-empty">
            <div>▣</div>
            <h3>No entity selected</h3>
            <p>Click a lineage card to inspect catalog metadata, DQC quality context, and technical identifiers.</p>
          </div>
        ) : (
          <div className="dg-details-body">
            <div className="dg-entity-heading">
              <span className="entity-icon-large">{getEntityIcon(selectedNode.type)}</span>
              <div>
                <em>{prettyType(selectedNode.type)}</em>
                <h3>{selectedNode.label}</h3>
              </div>
            </div>

            <div className={`dg-quality-box ${getQualityStatus(selectedNode).level}`}>
              <strong>{qualityText(selectedNode)}</strong>
              <span>{getQualityStatus(selectedNode).hint}</span>
            </div>

            <div className="dg-metadata-list">
              <div className="dg-meta-row"><span>node_id</span><code>{selectedNode.node_id || selectedNode.id}</code></div>
              <div className="dg-meta-row"><span>path</span><code>{pathFromNode(selectedNode) || "not available"}</code></div>
              {Object.entries(selectedNode.properties || {}).slice(0, 18).map(([key, value]) => (
                <div key={key} className="dg-meta-row">
                  <span>{key}</span>
                  <code>{value === null || value === undefined ? "null" : String(value)}</code>
                </div>
              ))}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
