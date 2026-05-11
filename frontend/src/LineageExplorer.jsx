import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";

import { fetchBusinessLineage, searchAssets } from "./api";
import { createStylesheet, graphToCytoscapeElements } from "./lineageUtils";

cytoscape.use(dagre);

export default function LineageExplorer() {
  const cyContainerRef = useRef(null);
  const cyRef = useRef(null);
  const lastTapRef = useRef({ time: 0, nodeId: null });

  const [nodeId, setNodeId] = useState("");
  const [depth, setDepth] = useState(2);
  const [graph, setGraph] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [error, setError] = useState("");

  async function loadGraph(id = nodeId) {
    if (!id.trim()) {
      setError("Enter a node_id first.");
      return;
    }

    setLoading(true);
    setError("");
    setSelectedNode(null);

    try {
      const data = await fetchBusinessLineage(id.trim(), depth);
      setGraph(data);
      renderGraph(data);
    } catch (err) {
      console.error(err);
      setError(
        err?.response?.data?.detail ||
          "Failed to load lineage. Check the node_id and backend."
      );
    } finally {
      setLoading(false);
    }
  }

  function renderGraph(data) {
    if (!cyContainerRef.current) return;

    const elements = graphToCytoscapeElements(data);

    if (cyRef.current) {
      cyRef.current.destroy();
    }

    const cy = cytoscape({
      container: cyContainerRef.current,
      elements,
      style: createStylesheet(data.root),
      layout: {
        name: "dagre",
        rankDir: "LR",
        nodeSep: 70,
        rankSep: 120,
        edgeSep: 20,
      },
      minZoom: 0.1,
      maxZoom: 3,
      wheelSensitivity: 0.15,
    });

    cy.on("tap", "node", (event) => {
      const node = event.target;
      setSelectedNode(node.data());

      const now = Date.now();
      const previous = lastTapRef.current;

      if (previous.nodeId === node.id() && now - previous.time < 350) {
        toggleCollapse(node);
      }

      lastTapRef.current = {
        time: now,
        nodeId: node.id(),
      };
    });

    cy.on("tap", (event) => {
      if (event.target === cy) {
        setSelectedNode(null);
      }
    });

    cyRef.current = cy;

    setTimeout(() => {
      cy.fit(undefined, 40);
    }, 100);
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

    cy.layout({
      name: "dagre",
      rankDir: "LR",
      nodeSep: 70,
      rankSep: 120,
      edgeSep: 20,
    }).run();
  }

  function resetCollapse() {
    const cy = cyRef.current;
    if (!cy) return;

    cy.elements().removeClass("hidden");
    cy.nodes().removeClass("collapsed");

    cy.layout({
      name: "dagre",
      rankDir: "LR",
      nodeSep: 70,
      rankSep: 120,
      edgeSep: 20,
    }).run();

    cy.fit(undefined, 40);
  }

  function fitGraph() {
    const cy = cyRef.current;
    if (!cy) return;
    cy.fit(undefined, 40);
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

  useEffect(() => {
    return () => {
      if (cyRef.current) {
        cyRef.current.destroy();
      }
    };
  }, []);

return (
  <div className="page">
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">DG</div>
        <div>
          <h1>DataGalaxy Lineage</h1>
          <p>Catalog graph exploration</p>
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
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search source, table, field..."
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSearch();
              }}
            />
            <button onClick={handleSearch}>Search</button>
          </div>

          {searchResults.length > 0 && (
            <div className="results">
              {searchResults.map((result) => (
                <button
                  key={result.id}
                  className="result"
                  onClick={() => selectSearchResult(result)}
                >
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
            onChange={(e) => setNodeId(e.target.value)}
            placeholder="Paste a Neo4j node_id here..."
            rows={4}
          />

          <label>Exploration depth</label>
          <select
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
          >
            <option value={1}>1 - Direct neighbors</option>
            <option value={2}>2 - Standard lineage</option>
            <option value={3}>3 - Extended lineage</option>
            <option value={4}>4 - Large graph</option>
            <option value={5}>5 - Very large graph</option>
          </select>

          <button className="primary" onClick={() => loadGraph()}>
            {loading ? "Loading lineage..." : "Explore lineage"}
          </button>

          <div className="actions">
            <button onClick={fitGraph}>Fit graph</button>
            <button onClick={resetCollapse}>Expand all</button>
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h3 className="panel-title">Legend</h3>
            <span className="panel-badge">Types</span>
          </div>

          <div className="legend">
            <div className="legend-item">
              <span className="legend-dot source" /> Source
            </div>
            <div className="legend-item">
              <span className="legend-dot container" /> Container
            </div>
            <div className="legend-item">
              <span className="legend-dot structure" /> Structure
            </div>
            <div className="legend-item">
              <span className="legend-dot field" /> Field
            </div>
            <div className="legend-item">
              <span className="legend-dot usage" /> Usage
            </div>
            <div className="legend-item">
              <span className="legend-dot term" /> Term
            </div>
          </div>
        </div>

        <div className="panel help">
          <div className="panel-header">
            <h3 className="panel-title">Controls</h3>
          </div>
          <p>Click an entity to inspect metadata.</p>
          <p>Double-click an entity to collapse or expand its neighbors.</p>
          <p>Scroll to zoom. Drag to move the graph.</p>
        </div>

        {graph && (
          <div className="panel stats">
            <div className="panel-header">
              <h3 className="panel-title">Graph summary</h3>
            </div>
            <p>Nodes: {graph.stats?.node_count ?? 0}</p>
            <p>Edges: {graph.stats?.edge_count ?? 0}</p>
          </div>
        )}

        {error && <div className="error">{error}</div>}
      </div>
    </aside>

    <main className="canvas-area">
      <div className="toolbar">
        <div className="toolbar-title">
          <strong>Lineage visualization</strong>
          <span>
            {graph
              ? `Root entity: ${graph.root}`
              : "Search an asset or paste a node_id to start exploring"}
          </span>
        </div>

        <div className="toolbar-pills">
          <span className="pill blue">Neo4j graph</span>
          <span className="pill">Business lineage</span>
        </div>
      </div>

      <div className="graph-layout">
        <div ref={cyContainerRef} className="cy-container" />

        <section className="details">
          <div className="details-header">
            <h2>Entity details</h2>
          </div>

          <div className="details-body">
            {!selectedNode && (
              <p className="muted">
                Select a lineage entity to view its catalog metadata,
                technical name, path, and governance attributes.
              </p>
            )}

            {selectedNode && (
              <>
                <div className="node-title">
                  <span className="node-type">{selectedNode.type}</span>
                  <strong>{selectedNode.label}</strong>
                </div>

                <div className="metadata">
                  {Object.entries(selectedNode.properties || {}).map(
                    ([key, value]) => (
                      <div key={key} className="metadata-row">
                        <span>{key}</span>
                        <code>
                          {value === null || value === undefined
                            ? "null"
                            : String(value)}
                        </code>
                      </div>
                    )
                  )}
                </div>
              </>
            )}
          </div>
        </section>
      </div>
    </main>
  </div>
);
}