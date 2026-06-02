import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { fetchLegacyQualityResults, fetchResolvedDqc, fetchUnresolvedDqc } from "../../../api";
import LineageCanvas from "./LineageCanvas";
import LineageLegend from "./LineageLegend";
import LineageMetadataPanel from "./LineageMetadataPanel";
import LineageSearchBar from "./LineageSearchBar";
import { useLineageExplorer } from "../hooks/useLineageExplorer";
import {
  buildQualityIndex,
  collectQualityForNode,
  safeQualityItems,
  type LineageQualityItem,
} from "../utils/lineageQuality";

export default function LineageExplorer() {
  const explorer = useLineageExplorer();
  const focused = explorer.graph.nodes.find((node) => node.id === explorer.graph.focusedNodeId);
  const [qualityItems, setQualityItems] = useState<LineageQualityItem[]>([]);
  const [qualityError, setQualityError] = useState("");
  const [leftPanelWidth, setLeftPanelWidth] = useState(320);
  const [rightPanelWidth, setRightPanelWidth] = useState(360);
  const resizeRef = useRef<{
    side: "left" | "right";
    startX: number;
    startWidth: number;
  } | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadQualityControls() {
      const [resolved, unresolved, legacy] = await Promise.allSettled([
        fetchResolvedDqc(1000),
        fetchUnresolvedDqc(1000),
        fetchLegacyQualityResults(1000),
      ]);
      if (cancelled) return;
      if (resolved.status === "rejected" && unresolved.status === "rejected") {
        setQualityError("Quality controls could not be loaded. The lineage graph is still available.");
      } else {
        setQualityError("");
      }
      const next = [
        ...(resolved.status === "fulfilled" ? safeQualityItems(resolved.value) : []),
        ...(unresolved.status === "fulfilled" ? safeQualityItems(unresolved.value) : []),
        ...(legacy.status === "fulfilled" ? safeQualityItems(legacy.value) : []),
      ];
      setQualityItems(next);
    }

    loadQualityControls().catch(() => {
      if (!cancelled) {
        setQualityItems([]);
        setQualityError("Quality controls could not be loaded. The lineage graph is still available.");
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    function handleMove(event: MouseEvent) {
      if (!resizeRef.current) return;
      event.preventDefault();
      const delta = event.clientX - resizeRef.current.startX;
      if (resizeRef.current.side === "left") {
        setLeftPanelWidth(Math.max(260, Math.min(520, resizeRef.current.startWidth + delta)));
        return;
      }
      setRightPanelWidth(Math.max(300, Math.min(620, resizeRef.current.startWidth - delta)));
    }

    function handleUp() {
      resizeRef.current = null;
      document.body.classList.remove("plex-resizing");
    }

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, []);

  function startResize(side: "left" | "right", event: ReactMouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    resizeRef.current = {
      side,
      startX: event.clientX,
      startWidth: side === "left" ? leftPanelWidth : rightPanelWidth,
    };
    document.body.classList.add("plex-resizing");
  }

  const qualityIndex = useMemo(() => buildQualityIndex(qualityItems), [qualityItems]);
  const qualityByNodeId = useMemo(() => {
    const mapping: Record<string, LineageQualityItem[]> = {};
    explorer.graph.nodes.forEach((node) => {
      mapping[node.id] = collectQualityForNode(node, qualityIndex);
    });
    return mapping;
  }, [explorer.graph.nodes, qualityIndex]);

  return (
    <div
      className="plex-shell"
      style={{
        ["--plex-left-width" as string]: `${leftPanelWidth}px`,
        ["--plex-right-width" as string]: `${rightPanelWidth}px`,
      }}
    >
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
        {qualityError && <div className="plex-error">{qualityError}</div>}
      </aside>
      <button
        type="button"
        className="plex-resize-handle left"
        onMouseDown={(event) => startResize("left", event)}
        aria-label="Resize search panel"
        title="Resize search panel"
      />

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
          qualityByNodeId={qualityByNodeId}
          loading={explorer.loadingExpansions}
          loadingSourceContexts={explorer.loadingSourceContexts}
          onFocus={explorer.focusNode}
          onMoveNode={explorer.moveNode}
          onExpand={explorer.expandNode}
          onExpandSourceContext={explorer.expandSourceContext}
          onHighlight={explorer.applyHighlight}
          onClearNodeHighlights={explorer.clearNodeHighlights}
          onClearAllHighlights={explorer.clearAllHighlights}
          onResetLayout={explorer.resetLayout}
        />
      </main>
      <button
        type="button"
        className="plex-resize-handle right"
        onMouseDown={(event) => startResize("right", event)}
        aria-label="Resize metadata panel"
        title="Resize metadata panel"
      />
      <LineageMetadataPanel node={focused} />
    </div>
  );
}
