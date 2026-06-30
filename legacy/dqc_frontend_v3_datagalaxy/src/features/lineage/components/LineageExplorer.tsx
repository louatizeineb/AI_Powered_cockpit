import LineageCanvas from "./LineageCanvas";
import LineageControlsTable from "./LineageControlsTable";
import LineageLegend from "./LineageLegend";
import LineageMetadataPanel from "./LineageMetadataPanel";
import LineageSearchBar from "./LineageSearchBar";
import { useLineageExplorer } from "../hooks/useLineageExplorer";

export default function LineageExplorer() {
  const explorer = useLineageExplorer();
  const focused = explorer.graph.nodes.find((node) => node.id === explorer.graph.focusedNodeId);

  return (
    <div className="plex-shell">
      <aside className="plex-sidebar">
        <div className="plex-brand">
          <strong>Lineage Explorer</strong>
          <span>Search, focus, expand, understand</span>
        </div>

        <LineageSearchBar
          query={explorer.query}
          searching={explorer.searching}
          results={explorer.searchResults}
          onQueryChange={explorer.setQuery}
          onSearch={explorer.runSearch}
          onSelect={explorer.selectResult}
        />

        <LineageLegend />

        <div className="plex-side-card">
          <span>Loaded nodes</span>
          <strong>{explorer.graph.nodes.length}</strong>
        </div>
        <div className="plex-side-card">
          <span>Loaded edges</span>
          <strong>{explorer.graph.edges.length}</strong>
        </div>

        {focused && (
          <div className="plex-inspector-mini">
            <small>Focused entity</small>
            <strong>{focused.label}</strong>
            <code>{focused.path || focused.node_id}</code>
          </div>
        )}

        {explorer.error && <div className="plex-error">{explorer.error}</div>}
      </aside>

      <main className="plex-main">
        <header className="plex-toolbar">
          <div>
            <strong>Progressive lineage story</strong>
            <span>Only one depth level is fetched each time you click a side expansion button.</span>
          </div>
          <div className="plex-toolbar-pills">
            <span>Lazy loading</span>
            <span>One-hop expansion</span>
          </div>
        </header>

        <LineageCanvas
          graph={explorer.graph}
          positions={explorer.positions}
          loading={explorer.loadingExpansions}
          onFocus={explorer.focusNode}
          onMoveNode={explorer.moveNode}
          onExpand={explorer.expandNode}
          onHighlight={explorer.applyHighlight}
          onClearNodeHighlights={explorer.clearNodeHighlights}
          onClearAllHighlights={explorer.clearAllHighlights}
          onResetLayout={explorer.resetLayout}
        />
        <LineageControlsTable node={focused} />
      </main>
      <LineageMetadataPanel node={focused} />
    </div>
  );
}
