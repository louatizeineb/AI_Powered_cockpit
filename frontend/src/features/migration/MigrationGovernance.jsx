import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";
import {
  Bot, Check, DatabaseZap, Gauge, LayoutDashboard, ListChecks,
  Maximize2, MessageCircle, PanelLeftClose, PanelLeftOpen,
  RefreshCcw, Rocket, Send, ZoomIn, ZoomOut,
} from "lucide-react";
import {
  askMigrationGovernanceAssistant,
  askGovernanceGraphRag,
  decideMigrationIssue,
  fetchGovernanceItems,
  fetchMigrationActivity,
  fetchMigrationAgentEvaluations,
  fetchMigrationExports,
  fetchMigrationOverview,
  fetchMigrationQueue,
  fetchSchemaColumns,
  fetchSchemaIntelligence,
  runMigrationAction,
} from "../../api";
import RecordInspector from "../../components/RecordInspector";

const NAV = [
  ["overview", "Overview", LayoutDashboard],
  ["validation", "Review issues", ListChecks],
  ["checks", "Release checks", Gauge],
  ["publish", "Publish", Rocket],
  ["agents", "Evidence trail", Bot],
];

const ISSUE_BUCKETS = [
  {
    id: "pending",
    label: "Needs decision",
    status: "pending",
    policy: "",
    description: "Open questions that need a human or policy decision.",
  },
  {
    id: "quarantine",
    label: "Quarantined",
    status: "approved",
    policy: "quarantine",
    description: "Kept for evidence, hidden from normal search and lineage.",
  },
  {
    id: "repair",
    label: "Needs repair",
    status: "pending",
    policy: "repair",
    description: "Cannot publish until repaired or explicitly resolved.",
  },
  {
    id: "repaired",
    label: "Repaired",
    status: "resolved",
    policy: "repair",
    description: "Repair evidence was accepted and no longer needs action.",
  },
  {
    id: "accept",
    label: "Accepted",
    status: "approved",
    policy: "accept",
    description: "Approved for the trusted graph.",
  },
  {
    id: "exclude",
    label: "Removed",
    status: "approved",
    policy: "exclude",
    description: "Retained as evidence, removed from trusted projection.",
  },
  {
    id: "block",
    label: "Blocked",
    status: "",
    policy: "block",
    description: "Explicit stop signs until a human changes the decision.",
  },
];

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function statusTone(value) {
  const status = String(value || "unknown").toLowerCase();
  if (["ready", "trusted", "published", "completed", "approved", "resolved"].includes(status)) return "good";
  if (["blocked", "failed", "hard_block", "repair", "rejected"].includes(status)) return "bad";
  if (["quarantine", "review_pending", "waiting_approval", "pending"].includes(status)) return "warn";
  return "neutral";
}

function StatusBadge({ value }) {
  return <span className={`mg-status ${statusTone(value)}`}>{String(value || "unknown").replaceAll("_", " ")}</span>;
}

function Metric({ label, value, tone = "", hint = "" }) {
  return (
    <div className={`metric-card ${tone}`}>
      <span>{label}</span>
      <strong>{formatNumber(value)}</strong>
      {hint && <small>{hint}</small>}
    </div>
  );
}

function issueTitle(item) {
  const type = String(item?.issue_type || item?.relationship_type || "Governance issue").replaceAll("_", " ");
  return type.charAt(0).toUpperCase() + type.slice(1);
}

function issueImpact(item) {
  const severity = String(item?.severity || "").toUpperCase();
  const policy = String(item?.publish_policy || item?.agent_proposed_policy || "").toLowerCase();
  if (severity === "ERROR" || ["block", "hard_block", "repair"].includes(policy)) return "Can block publish";
  if (policy === "quarantine") return "Hidden from normal search/lineage";
  if (policy === "accept") return "Can enter trusted graph after approval";
  if (policy === "exclude") return "Kept as evidence, not projected";
  return "Needs review";
}

function affectedIdentity(item) {
  return item?.node_id || item?.relationship_type || item?.raw_column_name || `${item?.src_node_id || "?"} -> ${item?.tgt_node_id || "?"}`;
}

function recommendation(item) {
  return item?.agent_proposed_policy || item?.publish_policy || item?.proposed_action || "review";
}

function percent(value) {
  if (value === undefined || value === null || value === "") return "-";
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function shortText(value, max = 92) {
  const text = String(value || "").trim();
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max - 1).trim()}...` : text;
}

function evidenceValue(value) {
  if (Array.isArray(value)) return value.filter(Boolean).join(" | ");
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value ?? "-");
}

function normalizeList(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.filter((item) => item !== null && item !== undefined && item !== "");
  return [value];
}

function shortNodeLabel(value, max = 16) {
  const text = String(value || "").trim();
  if (!text) return "-";
  return text.length > max ? `${text.slice(0, max - 1).trim()}...` : text;
}

function SchemaKgGraph({ tableName, columns, onSelectColumn }) {
  const [query, setQuery] = useState("");
  const [focusedName, setFocusedName] = useState("");
  const [selectedNode, setSelectedNode] = useState(null);
  const [detailsWidth, setDetailsWidth] = useState(280);
  const nvlRef = useRef(null);
  const filteredColumns = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return columns;
    return columns.filter((column) => {
      const haystack = [
        column.column_name,
        column.data_type_guess,
        normalizeList(column.warnings).join(" "),
        normalizeList(column.sample_values).join(" "),
      ].join(" ").toLowerCase();
      return haystack.includes(needle);
    });
  }, [columns, query]);
  const visibleColumns = filteredColumns.slice(0, 32);
  const focusedColumn = selectedNode?.kind === "column"
    ? visibleColumns.find((column) => column.column_name === selectedNode.column.column_name) || selectedNode.column
    : null;
  const selectedTableNode = selectedNode?.kind === "table" ? selectedNode : null;
  const highlightedColumnName = focusedColumn?.column_name || focusedName;
  const selectedGraphId = selectedTableNode
    ? `table:${tableName}`
    : focusedColumn
      ? `column:${tableName}:${focusedColumn.column_name}`
      : "";
  const graphLayoutKey = useMemo(
    () => `${tableName || "none"}:${visibleColumns.map((column) => column.column_name).join("|")}`,
    [tableName, visibleColumns],
  );

  const nvlGraph = useMemo(() => {
    if (!tableName) return { nodes: [], rels: [] };
    const tableId = `table:${tableName}`;
    const ringRadius = Math.max(260, Math.min(620, 130 + visibleColumns.length * 8));
    const nodes = [
      {
        id: tableId,
        caption: shortNodeLabel(tableName, 18),
        captionSize: 10,
        color: "#2563eb",
        selected: selectedGraphId === tableId,
        size: 34,
        x: 0,
        y: 0,
      },
      ...visibleColumns.map((column, index) => {
        const id = `column:${tableName}:${column.column_name}`;
        const hasWarnings = normalizeList(column.warnings).length > 0;
        const angle = ((Math.PI * 2) / Math.max(visibleColumns.length, 1)) * index - Math.PI / 2;
        return {
          id,
          caption: shortNodeLabel(column.column_name, 14),
          captionSize: 9,
          color: hasWarnings ? "#f59e0b" : "#14b8a6",
          selected: selectedGraphId === id,
          size: hasWarnings ? 25 : 22,
          x: Math.cos(angle) * ringRadius,
          y: Math.sin(angle) * ringRadius,
        };
      }),
    ];
    const rels = visibleColumns.map((column) => {
      const columnId = `column:${tableName}:${column.column_name}`;
      return {
        id: `rel:${tableName}:${column.column_name}`,
        from: tableId,
        to: columnId,
        type: "HAS_COLUMN",
        color: "rgba(71, 85, 105, 0.32)",
        width: 1.25,
      };
    });
    return { nodes, rels };
  }, [selectedGraphId, tableName, visibleColumns]);

  useEffect(() => {
    setFocusedName("");
    setSelectedNode(null);
  }, [tableName]);

  useEffect(() => {
    if (!nvlGraph.nodes.length) return undefined;
    const nodeIds = nvlGraph.nodes.map((node) => node.id);
    const timeoutId = window.setTimeout(() => {
      nvlRef.current?.fit?.(nodeIds);
    }, 180);
    return () => window.clearTimeout(timeoutId);
  }, [graphLayoutKey]);

  function selectColumn(column) {
    setFocusedName(column.column_name);
    setSelectedNode({ kind: "column", column });
  }

  function selectTableNode() {
    setFocusedName("");
    setSelectedNode({ kind: "table", tableName, columnCount: columns.length });
  }

  function openFullMetadata() {
    if (focusedColumn) {
      onSelectColumn({
        ...focusedColumn,
        kg_node_kind: "Column",
        kg_parent_table: tableName,
        kg_relationship: "HAS_COLUMN",
      });
      return;
    }
    if (selectedTableNode) {
      onSelectColumn({
        kg_node_kind: "Table",
        raw_table_name: tableName,
        column_count: columns.length,
      });
    }
  }

  function resetView() {
    nvlRef.current?.restart?.();
    nvlRef.current?.fit?.(nvlGraph.nodes.map((node) => node.id));
  }

  function fitGraph() {
    nvlRef.current?.fit?.(nvlGraph.nodes.map((node) => node.id));
  }

  function zoomGraph(delta) {
    const currentScale = nvlRef.current?.getScale?.() || 1;
    nvlRef.current?.setZoom?.(Math.max(0.35, Math.min(2.5, currentScale + delta)));
  }

  function handleNvlNodeClick(node) {
    if (!node?.id || !tableName) return;
    if (node.id === `table:${tableName}`) {
      selectTableNode();
      return;
    }
    const prefix = `column:${tableName}:`;
    if (!node.id.startsWith(prefix)) return;
    const columnName = node.id.slice(prefix.length);
    const column = visibleColumns.find((item) => item.column_name === columnName) || columns.find((item) => item.column_name === columnName);
    if (column) selectColumn(column);
  }

  function resizeDetails(event) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = detailsWidth;
    function onMove(moveEvent) {
      setDetailsWidth(Math.max(190, Math.min(420, startWidth + moveEvent.clientX - startX)));
    }
    function onUp() {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  return (
    <div className="dq-card mg-schema-kg-card">
      <div className="dq-card-head compact">
        <div>
          <h3>Schema Intelligence KG</h3>
          <p className="muted">Neo4j-style table-to-column view. Columns only connect to the table through HAS_COLUMN.</p>
        </div>
        <div className="mg-kg-toolbar">
          <button type="button" title="Fit graph" onClick={fitGraph}><Maximize2 size={14} />Fit</button>
          <button type="button" title="Zoom out" onClick={() => zoomGraph(-0.16)}><ZoomOut size={14} /></button>
          <button type="button" title="Zoom in" onClick={() => zoomGraph(0.16)}><ZoomIn size={14} /></button>
          <button type="button" title="Reset graph layout" onClick={resetView}><Maximize2 size={14} />Reset</button>
          <span>{formatNumber(columns.length)} columns</span>
          <span>{formatNumber(visibleColumns.length)} shown</span>
        </div>
      </div>

      <div
        className={`mg-schema-kg-shell ${selectedNode ? "has-node-details" : ""}`}
        style={{ "--mg-node-details-width": `${detailsWidth}px` }}
      >
        <aside className="mg-schema-table-rail">
          <label>
            Search columns
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Column, type, sample..." />
          </label>
          <div className="mg-kg-legend">
            <span><i className="table" />Table node</span>
            <span><i className="column" />Column node</span>
            <span><i className="warn" />Warnings</span>
          </div>
          <div className="mg-kg-mini-list">
            {visibleColumns.map((column) => (
              <button
                type="button"
                key={column.column_name}
                className={highlightedColumnName === column.column_name ? "active" : ""}
                onClick={() => setFocusedName(column.column_name)}
              >
                <strong>{column.column_name}</strong>
                <span>{column.data_type_guess || "unknown type"}</span>
              </button>
            ))}
          </div>
        </aside>

        <div
          className="mg-schema-kg-canvas"
          aria-label="Schema graph canvas"
        >
          {!tableName && <div className="mg-kg-empty">Select a table to explore its Schema KG.</div>}
          {!!tableName && <>
            <div className="mg-nvl-frame">
              <InteractiveNvlWrapper
                ref={nvlRef}
                nodes={nvlGraph.nodes}
                rels={nvlGraph.rels}
                layout="free"
                nvlOptions={{
                  disableTelemetry: true,
                  initialZoom: 0.72,
                  renderer: "canvas",
                  relationshipThreshold: 0,
                  styling: {
                    defaultRelationshipColor: "rgba(71, 85, 105, 0.32)",
                    nodeDefaultBorderColor: "rgba(15, 23, 42, 0.16)",
                    selectedBorderColor: "#14b8a6",
                    selectedInnerBorderColor: "#ffffff",
                    dropShadowColor: "rgba(15, 23, 42, 0.22)",
                  },
                }}
                mouseEventCallbacks={{
                  onCanvasClick: () => setSelectedNode(null),
                  onNodeClick: handleNvlNodeClick,
                }}
                interactionOptions={{
                  drawShadowOnHover: true,
                }}
              />
            </div>
            <div className="mg-kg-hint">Drag nodes, pan the canvas, and use the wheel to zoom.</div>
            {filteredColumns.length > visibleColumns.length && <div className="mg-kg-overflow">+{filteredColumns.length - visibleColumns.length} more columns. Search to narrow the graph.</div>}
          </>}
        </div>

        {selectedNode && <button className="mg-kg-resize-handle" type="button" aria-label="Resize details panel" onPointerDown={resizeDetails} />}
        {selectedNode && <aside className="mg-schema-node-details">
          <span className="next-overline">Selected metadata</span>
          {selectedTableNode && <>
            <h4>{selectedTableNode.tableName}</h4>
            <dl>
              <div><dt>Node</dt><dd>Table</dd></div>
              <div><dt>Relationships</dt><dd>{formatNumber(selectedTableNode.columnCount)} HAS_COLUMN edges</dd></div>
              <div><dt>Visible columns</dt><dd>{formatNumber(visibleColumns.length)} of {formatNumber(filteredColumns.length)}</dd></div>
              <div><dt>Warnings</dt><dd>{formatNumber(columns.filter((column) => normalizeList(column.warnings).length).length)} columns with warnings</dd></div>
            </dl>
            <button type="button" onClick={openFullMetadata}>Open full metadata</button>
          </>}
          {focusedColumn && <>
            <h4>{focusedColumn.column_name}</h4>
            <dl>
              <div><dt>Relationship</dt><dd>({tableName}) -[HAS_COLUMN]-&gt; ({focusedColumn.column_name})</dd></div>
              <div><dt>Type guess</dt><dd>{focusedColumn.data_type_guess || "unknown"}</dd></div>
              <div><dt>Non-null</dt><dd>{formatNumber(focusedColumn.non_null_count)}</dd></div>
              <div><dt>Nulls</dt><dd>{formatNumber(focusedColumn.null_count)}</dd></div>
              <div><dt>Distinct</dt><dd>{formatNumber(focusedColumn.distinct_count)}</dd></div>
              <div><dt>Samples</dt><dd>{normalizeList(focusedColumn.sample_values).slice(0, 4).join(", ") || "-"}</dd></div>
              <div><dt>Warnings</dt><dd>{normalizeList(focusedColumn.warnings).join(", ") || "None"}</dd></div>
            </dl>
            <button type="button" onClick={openFullMetadata}>Open full metadata</button>
          </>}
        </aside>}
      </div>
    </div>
  );
}

function IssueEvidenceInspector({ item, onClose, reviewNote, setReviewNote, onAsk, onResolve }) {
  const evidence = item.evidence || {};
  const question = item.agent_question || item.human_question || "What decision should be applied to this issue?";
  const currentPolicy = item.publish_policy || "not decided";
  const agentPolicy = item.agent_proposed_policy || item.proposed_action || "review";
  const agentDiffers = item.agent_proposed_policy && item.publish_policy && item.agent_proposed_policy !== item.publish_policy;
  const evidenceKeys = [
    ["Observed roles", evidence.observed_roles],
    ["Canonical role", evidence.canonical_role || evidence.canonical_roles],
    ["Conflict fields", evidence.conflict_fields],
    ["Labels", evidence.labels],
    ["Technical names", evidence.technical_names],
    ["Paths", evidence.paths],
    ["Parent nodes", evidence.parent_node_ids],
    ["Source tables", evidence.source_tables],
    ["Policy", evidence.policy],
  ].filter(([, value]) => value !== undefined && value !== null && evidenceValue(value) !== "-");

  return (
    <aside className="record-inspector mg-issue-inspector" aria-label="Issue decision inspector">
      <header className="record-inspector-header">
        <div>
          <span>Decision workspace</span>
          <h3>{issueTitle(item)}</h3>
          <p>{affectedIdentity(item)}</p>
        </div>
        <div className="record-inspector-actions">
          <button type="button" title="Close inspector" aria-label="Close inspector" onClick={onClose}>Close</button>
        </div>
      </header>

      <div className="mg-issue-inspector-content">
        <section className="mg-decision-summary">
          <div><span>Current decision</span><StatusBadge value={currentPolicy} /></div>
          <div><span>Queue status</span><StatusBadge value={item.queue_status} /></div>
          <div><span>Agent suggests</span><StatusBadge value={agentPolicy} /></div>
          <div><span>Confidence</span><strong>{percent(item.agent_confidence ?? item.confidence)}</strong></div>
        </section>

        {agentDiffers && <div className="mg-agent-disagreement">
          Agent recommendation differs from the current decision. Review the question below before publishing this as trusted.
        </div>}

        <section className="mg-human-answer-card">
          <span className="next-overline">Question to answer</span>
          <h4>{question}</h4>
          <label>
            Human answer / repair evidence
            <textarea
              rows={5}
              value={reviewNote}
              onChange={(event) => setReviewNote(event.target.value)}
              placeholder="Example: Domain owner confirmed CON and VAL are distinct KQIs. Split required, keep quarantined until repair tool creates separate node IDs."
            />
          </label>
          <div className="mg-resolution-actions inspector-actions">
            <button type="button" onClick={() => onAsk(item)}>Ask agent with this issue</button>
            <button type="button" onClick={() => onResolve(item, "accept")}>Accept into trusted</button>
            <button type="button" onClick={() => onResolve(item, "quarantine")}>Keep quarantined</button>
            <button type="button" onClick={() => onResolve(item, "exclude")}>Remove from trusted</button>
            <button type="button" onClick={() => onResolve(item, "repair")}>Needs repair</button>
            <button type="button" onClick={() => onResolve(item, "resolved", `${reviewNote} Repair evidence accepted by reviewer.`)}>Mark repaired</button>
            <button type="button" className="danger" onClick={() => onResolve(item, "block")}>Block publish</button>
          </div>
        </section>

        <section className="mg-agent-card">
          <h4>Agent reasoning</h4>
          <p>{item.agent_rationale || item.rationale || "No agent rationale was recorded for this issue."}</p>
          {!!item.agent_missing_evidence?.length && <>
            <h5>Missing evidence</h5>
            <ul>{item.agent_missing_evidence.map((value) => <li key={value}>{value}</li>)}</ul>
          </>}
        </section>

        {!!evidenceKeys.length && <section className="mg-evidence-card">
          <h4>Useful evidence</h4>
          <dl>
            {evidenceKeys.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{evidenceValue(value)}</dd></div>)}
          </dl>
        </section>}

        <details className="mg-raw-record">
          <summary>Raw record</summary>
          <pre>{JSON.stringify(item, null, 2)}</pre>
        </details>
      </div>
    </aside>
  );
}

function EvidenceInspector({ item, onClose, reviewNote, setReviewNote, onAsk, onResolve }) {
  if (!item) return null;
  if (item.issue_id) {
    return (
      <IssueEvidenceInspector
        item={item}
        onClose={onClose}
        reviewNote={reviewNote}
        setReviewNote={setReviewNote}
        onAsk={onAsk}
        onResolve={onResolve}
      />
    );
  }
  return <RecordInspector item={item} title="Governance evidence" eyebrow="Decision context" onClose={onClose} />;
}

export default function MigrationGovernance() {
  const [tab, setTab] = useState("overview");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(330);
  const [exports, setExports] = useState([]);
  const [exportId, setExportId] = useState("");
  const [overview, setOverview] = useState(null);
  const [queue, setQueue] = useState({ items: [], total: 0 });
  const [activity, setActivity] = useState({ agent_runs: [], tool_executions: [], approvals: [] });
  const [evaluations, setEvaluations] = useState({ latest_run: null, runs: [], scores: [], case_count: 0 });
  const [schema, setSchema] = useState({ tables: [], mapping_proposals: [] });
  const [quarantine, setQuarantine] = useState({ items: [], total: 0 });
  const [selectedTable, setSelectedTable] = useState("");
  const [columns, setColumns] = useState([]);
  const [selected, setSelected] = useState(null);
  const [issueBucket, setIssueBucket] = useState("pending");
  const [reviewer, setReviewer] = useState("louat");
  const [reviewNote, setReviewNote] = useState("Reviewed in Migration Governance cockpit.");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [question, setQuestion] = useState("Why is publication blocked, and what evidence should be reviewed next?");
  const [answer, setAnswer] = useState(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantInput, setAssistantInput] = useState("");
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [assistantSuggestions, setAssistantSuggestions] = useState([
    "What should I do next before publish?",
    "Explain this screen in simple terms.",
    "What is trusted versus quarantine?",
  ]);
  const [assistantMessages, setAssistantMessages] = useState([
    {
      role: "assistant",
      content: "Ask me what this workspace means, why an item is blocked, what a decision does, or what should happen before publish. I use the governance evidence from this export.",
      citations: [],
      mode: "intro",
    },
  ]);
  const effectiveSidebarWidth = sidebarCollapsed ? 72 : sidebarWidth;

  useEffect(() => {
    function syncSidebarToViewport() {
      const graphHeavyScreen = tab === "checks";
      const collapseAt = graphHeavyScreen ? 1380 : 1120;
      if (window.innerWidth < collapseAt) {
        setSidebarCollapsed(true);
        return;
      }
      setSidebarCollapsed((collapsed) => (collapsed && sidebarWidth >= 260 ? false : collapsed));
    }
    syncSidebarToViewport();
    window.addEventListener("resize", syncSidebarToViewport);
    return () => window.removeEventListener("resize", syncSidebarToViewport);
  }, [sidebarWidth, tab]);

  const loadExports = useCallback(() => {
    fetchMigrationExports()
      .then((payload) => {
        const items = payload.items || [];
        setExports(items);
        if (items.length) setExportId((current) => current || items[0].export_id);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => { loadExports(); }, [loadExports]);

  const refresh = useCallback(async () => {
    if (!exportId) return;
    setBusy(true);
    setError("");
    const bucket = ISSUE_BUCKETS.find((item) => item.id === issueBucket) || ISSUE_BUCKETS[0];
    try {
      const [nextOverview, nextQueue, nextActivity, nextEvaluations, nextSchema] = await Promise.all([
        fetchMigrationOverview(exportId),
        fetchMigrationQueue(exportId, {
          status: bucket.status,
          publishPolicy: bucket.policy,
          limit: 200,
        }),
        fetchMigrationActivity(exportId),
        fetchMigrationAgentEvaluations(exportId),
        fetchSchemaIntelligence(exportId),
      ]);
      setOverview(nextOverview);
      setQueue(nextQueue);
      setActivity(nextActivity);
      setEvaluations(nextEvaluations);
      setSchema(nextSchema);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }, [exportId, issueBucket]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (tab !== "checks" || !exportId) return;
    fetchGovernanceItems(exportId, { state: "quarantine", kind: "object", limit: 100 })
      .then(setQuarantine)
      .catch((err) => setError(err.message));
  }, [tab, exportId]);

  const publication = overview?.publication || {};
  const workflow = overview?.workflow || {};
  const objectCounts = publication.object_counts || {};
  const relationshipCounts = publication.relationship_counts || {};
  const blockers = publication.hard_blockers || [];
  const releaseStatus = blockers.length ? (publication.status || workflow.status) : "ready";
  const benchmark = overview?.benchmark || {};
  const publishReport = overview?.publish_report || {};
  const queueSummary = useMemo(() => {
    const result = {};
    (overview?.queue_counts || []).forEach((item) => {
      result[item.queue_status] = (result[item.queue_status] || 0) + Number(item.count || 0);
    });
    return result;
  }, [overview]);
  const queuePolicySummary = useMemo(() => {
    const result = {};
    (overview?.queue_counts || []).forEach((item) => {
      const policy = item.publish_policy || "none";
      const status = item.queue_status || "none";
      const count = Number(item.count || 0);
      result[policy] = (result[policy] || 0) + count;
      result[`${status}:${policy}`] = (result[`${status}:${policy}`] || 0) + count;
    });
    return result;
  }, [overview]);
  const currentBucket = ISSUE_BUCKETS.find((item) => item.id === issueBucket) || ISSUE_BUCKETS[0];
  const publishBlockers = publishReport.blockers || blockers;
  const benchmarkReady = String(benchmark.status || "").toLowerCase() === "ready";
  const publishDryRunReady = ["ready", "ready_to_publish"].includes(String(publishReport.status || "").toLowerCase());
  const publishDisabledReason = publishBlockers.length
    ? `${publishBlockers.length} blocker${publishBlockers.length === 1 ? "" : "s"} remain`
    : !benchmarkReady
      ? "search benchmark has not passed"
      : !publishDryRunReady
        ? "run publish dry-run first"
        : "";
  const nextAction = useMemo(() => {
    if (!exports.length) {
      return {
        status: "Setup needed",
        title: "No migration export is ready for governance",
        detail: "Register an export or start a workflow, then this workspace will show release readiness.",
        action: "Refresh exports",
        tab: "exports",
      };
    }
    if (blockers.length) {
      return {
        status: "Not ready",
        title: `${blockers.length} hard blocker${blockers.length === 1 ? "" : "s"} need review`,
        detail: "Start with blocking evidence in Review issues, then recalculate readiness.",
        action: "Review blockers",
        tab: "validation",
      };
    }
    if (Number(queueSummary.pending || 0) > 0) {
      return {
        status: "Review needed",
        title: `${formatNumber(queueSummary.pending)} pending issue${Number(queueSummary.pending) === 1 ? "" : "s"} need a decision`,
        detail: "Approve trusted evidence, quarantine bounded uncertainty, or mark repair items before release.",
        action: "Open review issues",
        tab: "validation",
      };
    }
    if (!benchmarkReady) {
      return {
        status: "Check needed",
        title: "Search benchmark has not passed yet",
        detail: "Refresh the candidate search index and run the benchmark before publication.",
        action: "Open release checks",
        tab: "checks",
      };
    }
    if (!publishDryRunReady) {
      return {
        status: "Almost ready",
        title: "Run publish dry-run before activation",
        detail: "Dry-run verifies gates and rollback evidence without changing the active graph.",
        action: "Open publish",
        tab: "publish",
      };
    }
    return {
      status: "Ready",
      title: "Trusted graph is ready for explicit approval",
      detail: "The next action is controlled publication by an approved reviewer.",
      action: "Publish review",
      tab: "publish",
    };
  }, [exports.length, blockers.length, queueSummary.pending, benchmarkReady, publishDryRunReady]);

  async function chooseTable(tableName) {
    setSelectedTable(tableName);
    setBusy(true);
    try {
      const payload = await fetchSchemaColumns(exportId, tableName);
      setColumns(payload.columns || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function decide(item, decision) {
    setBusy(true);
    setError("");
    try {
      await decideMigrationIssue(exportId, item.issue_id, { decision, decided_by: reviewer, rationale: reviewNote });
      setNotice(`${item.issue_id} marked ${decision}. Refresh policy before publish review.`);
      setSelected(null);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function decisionCopy(decision) {
    return {
      accept: "Accepted into trusted graph. Recalculate readiness next.",
      quarantine: "Kept in quarantine. Recalculate readiness next.",
      exclude: "Removed from trusted projection. Recalculate readiness next.",
      repair: "Marked as needing repair. It will keep blocking until resolved.",
      resolved: "Marked repaired/resolved. Recalculate readiness next.",
      block: "Blocked for human approval. It will stop publication.",
    }[decision] || "Decision recorded. Recalculate readiness next.";
  }

  async function resolveIssue(item, decision, rationale = reviewNote) {
    setBusy(true);
    setError("");
    try {
      await decideMigrationIssue(exportId, item.issue_id, { decision, decided_by: reviewer, rationale });
      setNotice(`${item.issue_id} ${decisionCopy(decision)}`);
      setSelected(null);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function runAction(action) {
    if (action === "publish" && !window.confirm("Publish the trusted graph slice? All gates will still be enforced.")) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await runMigrationAction(exportId, action, reviewer);
      setNotice(`${action} completed as tool execution ${result.result?.execution_id || "recorded"}.`);
      await refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function askGraphRag() {
    setBusy(true);
    try {
      setAnswer(await askGovernanceGraphRag(exportId, question, selected?.issue_id || selected?.node_id || null));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function askAssistant(promptText = assistantInput, contextItem = selected) {
    const message = String(promptText || "").trim();
    if (!message || !exportId) return;
    const nextMessages = [...assistantMessages, { role: "user", content: message }];
    setAssistantMessages(nextMessages);
    setAssistantInput("");
    setAssistantBusy(true);
    setError("");
    try {
      const response = await askMigrationGovernanceAssistant(exportId, {
        message,
        screen: tab,
        subject: contextItem?.issue_id || contextItem?.node_id || contextItem?.execution_id || contextItem?.event_id || null,
        selected_item: contextItem || null,
        history: nextMessages.slice(-6).map((item) => ({ role: item.role, content: item.content })),
        use_llm: true,
      });
      setAssistantMessages([
        ...nextMessages,
        {
          role: "assistant",
          content: response.answer,
          citations: response.citations || [],
          mode: response.mode,
        },
      ]);
      if (response.suggested_questions?.length) setAssistantSuggestions(response.suggested_questions.slice(0, 5));
    } catch (err) {
      setAssistantMessages([
        ...nextMessages,
        {
          role: "assistant",
          content: `I could not read the governance evidence yet: ${err.message}`,
          citations: [],
          mode: "error",
        },
      ]);
    } finally {
      setAssistantBusy(false);
    }
  }

  function askIssueAssistant(item) {
    setSelected(item);
    setAssistantOpen(true);
    const prompt = [
      item.agent_question || item.human_question || "How should I decide this issue?",
      `Explain issue ${item.issue_id}.`,
      "Use the evidence, GraphRAG/provenance context, and tell me whether this should be accepted, quarantined, removed, repaired, or blocked.",
    ].join(" ");
    askAssistant(prompt, item);
  }

  function navigateTab(nextTab) {
    setSelected(null);
    setTab(nextTab);
  }

  function toggleSidebar() {
    setSidebarCollapsed((value) => {
      if (value) setSidebarWidth((width) => Math.max(width, 260));
      return !value;
    });
  }

  function startSidebarResize(event) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = effectiveSidebarWidth;
    document.body.classList.add("mg-sidebar-resizing");
    function onMove(moveEvent) {
      const nextWidth = Math.max(72, Math.min(430, startWidth + moveEvent.clientX - startX));
      if (nextWidth <= 96) {
        setSidebarCollapsed(true);
        return;
      }
      setSidebarCollapsed(false);
      setSidebarWidth(nextWidth);
    }
    function onUp() {
      document.body.classList.remove("mg-sidebar-resizing");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  return (
    <div
      className={`dq-shell mg-shell next-workspace ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}
      style={{ "--mg-sidebar-width": `${effectiveSidebarWidth}px` }}
    >
      <aside className="dq-sidebar next-context-nav">
        <div className="dq-brand next-context-heading">
          <div className="dq-brand-mark next-context-icon"><DatabaseZap size={19} /></div>
          <div><h1>Governance</h1></div>
          <button
            type="button"
            className="mg-sidebar-toggle"
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={toggleSidebar}
          >
            {sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
        </div>
        <div className="mg-export-picker">
          <label>Active export</label>
          <select value={exportId} onChange={(event) => setExportId(event.target.value)}>
            {!exports.length && <option value="">No export registered</option>}
            {exports.map((item) => <option key={item.export_id} value={item.export_id}>{item.export_id}</option>)}
          </select>
          <div className="mg-inline-status"><StatusBadge value={workflow.status || publication.status} /><small>{workflow.current_phase || "not started"}</small></div>
        </div>
        <nav className="dq-nav">
          {NAV.map(([id, label, Icon]) => <button type="button" key={id} className={tab === id ? "active" : ""} title={label} onClick={() => navigateTab(id)}><Icon size={17} /><span>{label}</span>{id === "validation" && queueSummary.pending > 0 && <small>{queueSummary.pending}</small>}</button>)}
        </nav>
        {error && <div className="dq-error">{error}</div>}
      </aside>
      <button
        type="button"
        className="mg-sidebar-resize-handle"
        aria-label="Resize governance sidebar"
        title="Resize sidebar"
        onPointerDown={startSidebarResize}
      />

      <main className="dq-main mg-main next-workspace-main">
        <header className="mg-header">
          <div><span className="next-overline">Metadata release</span><h2>{NAV.find(([id]) => id === tab)?.[1] || "Overview"}</h2><p>{exportId || "No export registered"} - guided trusted publication</p></div>
          <div className="mg-header-status">
            <StatusBadge value={releaseStatus} />
            <span>Graph v{overview?.search_state?.active_graph_version ?? 0}</span>
            <button className="mg-screen-refresh" title={`Refresh ${NAV.find(([id]) => id === tab)?.[1] || "screen"}`} onClick={refresh} disabled={busy || !exportId}>
              <RefreshCcw size={15} />{busy ? "Refreshing" : "Refresh"}
            </button>
            <button className="mg-assistant-toggle" onClick={() => setAssistantOpen((value) => !value)}><MessageCircle size={15} />Assistant</button>
          </div>
        </header>
        {notice && <div className="mg-notice">{notice}</div>}
        {!exports.length && <section className="mg-empty-state">
          <div className="mg-empty-icon"><DatabaseZap size={24} /></div>
          <div><span className="next-overline">Setup needed</span><h3>No migration export is ready for governance</h3><p>Register an export or start a workflow first. Once an export exists, this workspace will show review issues, release checks, publish readiness, and evidence history.</p></div>
          <div className="mg-setup-grid">
            <span>Backend<strong>{error ? "needs attention" : "connected"}</strong></span>
            <span>Migration database<strong>{error ? "check config" : "available"}</strong></span>
            <span>Exports<strong>0</strong></span>
            <span>Workflow<strong>not started</strong></span>
          </div>
        </section>}

        {!!exports.length && tab === "overview" && <>
          <section className={`mg-release-callout ${nextAction.status === "Ready" ? "ready" : nextAction.status === "Not ready" ? "blocked" : "review"}`}>
            <div className="mg-release-icon">{nextAction.status === "Ready" ? <Check size={22} /> : <ListChecks size={22} />}</div>
            <div><span className="next-overline">Can I publish? {nextAction.status}</span><h3>{nextAction.title}</h3><p>{nextAction.detail}</p></div>
            <button type="button" onClick={() => nextAction.tab === "exports" ? loadExports() : navigateTab(nextAction.tab)}>{nextAction.action}</button>
          </section>
          <section className="mg-release-steps" aria-label="Release progress">
            {["Understand export", "Review issues", "Check trusted graph", "Verify search", "Publish"].map((label, index) => <div key={label} className="complete"><span><Check size={13} /></span><strong>{label}</strong>{index < 4 && <i />}</div>)}
          </section>
          <section className="dq-kpi-grid mg-kpis">
            <Metric label="Trusted objects" value={objectCounts.trusted} tone="good" />
            <Metric label="Quarantined objects" value={objectCounts.quarantine} tone="warn" />
            <Metric label="Trusted relationships" value={relationshipCounts.trusted} tone="good" />
            <Metric label="Hard blockers" value={blockers.length} tone={blockers.length ? "bad" : "good"} />
          </section>
          <section className="dq-grid-2 mg-section">
            <div className="dq-card"><div className="dq-card-head compact"><h3>Workflow</h3><StatusBadge value={workflow.status} /></div>
              <div className="mg-facts"><span>Current phase<strong>{workflow.current_phase || "-"}</strong></span><span>Run ID<strong>{workflow.run_id || "-"}</strong></span><span>Updated<strong>{formatDate(workflow.updated_at)}</strong></span><span>Contract<strong>{overview?.export?.contract_version || "-"}</strong></span></div>
            </div>
            <div className="dq-card"><div className="dq-card-head compact"><h3>Decision queue</h3><span>{queue.total} issues</span></div>
              <div className="mg-facts"><span>Approved<strong>{formatNumber(queueSummary.approved)}</strong></span><span>Pending<strong>{formatNumber(queueSummary.pending)}</strong></span><span>Resolved<strong>{formatNumber(queueSummary.resolved)}</strong></span><span>Quarantine<strong>{formatNumber(objectCounts.quarantine)}</strong></span></div>
            </div>
            <div className="dq-card span-2"><h3>Remaining hard blockers</h3>
              {!blockers.length && <p className="muted">No structural blocker remains.</p>}
              <div className="mg-blocker-list">{blockers.map((item, index) => <button key={item.issue_id || index} onClick={() => setSelected(item)}><StatusBadge value="hard_block" /><strong>{item.relationship_type || item.category || "Structural issue"}</strong><span>{item.reason || item.message}</span></button>)}</div>
            </div>
          </section>
        </>}

        {!!exports.length && tab === "checks" && <section className="mg-schema-layout mg-release-checks">
          <div className="dq-card"><div className="dq-card-head compact"><div><h3>Export structure</h3><p className="muted">Tables and columns understood before release.</p></div><span>{schema.tables.length} tables</span></div>
            <div className="mg-table-list">{schema.tables.map((table) => <button className={selectedTable === table.raw_table_name ? "active" : ""} key={table.raw_table_name} onClick={() => chooseTable(table.raw_table_name)}><strong>{table.raw_table_name}</strong><span>{table.column_count} columns</span></button>)}</div>
          </div>
          <SchemaKgGraph tableName={selectedTable} columns={columns} onSelectColumn={setSelected} />
          <div className="dq-card span-2"><div className="dq-card-head compact"><div><h3>Schema mapping review</h3><p className="muted">Contract mismatches that may require steward approval.</p></div><span>{schema.mapping_proposals.length}</span></div>
            <div className="dq-table-wrap"><table className="dq-table mg-table"><thead><tr><th>Table</th><th>Column</th><th>Proposal</th><th>Confidence</th><th>Status</th><th>Question</th></tr></thead><tbody>{schema.mapping_proposals.map((item) => <tr key={item.id} onClick={() => setSelected(item)}><td>{item.raw_table_name}</td><td>{item.raw_column_name}</td><td>{item.proposed_action}</td><td>{Math.round(Number(item.confidence) * 100)}%</td><td><StatusBadge value={item.status} /></td><td>{item.human_question}</td></tr>)}</tbody></table></div>
          </div>
          <div className="dq-card"><div className="dq-card-head compact"><div><h3>Trusted graph check</h3><p className="muted">Validate the graph users would see.</p></div><StatusBadge value={overview?.publish_report?.status || "not checked"} /></div><div className="mg-facts"><span>Trusted objects<strong>{formatNumber(objectCounts.trusted)}</strong></span><span>Trusted relationships<strong>{formatNumber(relationshipCounts.trusted)}</strong></span><span>Invalid endpoints<strong>0</strong></span><span>Graph version<strong>{overview?.search_state?.active_graph_version ?? 0}</strong></span></div><button className="primary xl" onClick={() => runAction("candidate-dry-run")}>Validate trusted graph</button></div>
          <div className="dq-card"><div className="dq-card-head compact"><div><h3>Quarantine view</h3><p className="muted">Uncertain evidence retained but hidden from normal search and lineage.</p></div><span>{formatNumber(quarantine.total)}</span></div><button onClick={() => runAction("enforce-trusted-graph")}>Apply trusted-only view</button><div className="mg-compact-list">{quarantine.items.slice(0, 8).map((item) => <button key={`${item.node_id}:${item.object_type}`} onClick={() => setSelected(item)}><strong>{item.node_id}</strong><span>{item.object_type}</span><small>{item.publication_reason}</small></button>)}</div></div>
          <div className="dq-card span-2"><div className="dq-card-head compact"><div><h3>Search check</h3><p className="muted">Candidate search should be fast and return the trusted graph version.</p></div><div className="dq-card-actions"><StatusBadge value={benchmark.status || "not run"} /><button onClick={() => runAction("activate-candidate-search")}>Refresh candidate index</button><button onClick={() => runAction("benchmark")}>Run benchmark</button></div></div>
            <div className="mg-facts mg-benchmark-facts"><span>Cold p95<strong>{benchmark.acceptance?.cold_p95_ms ?? "-"} ms</strong></span><span>Warm p95<strong>{benchmark.acceptance?.warm_p95_ms ?? "-"} ms</strong></span><span>Graph version<strong>{overview?.search_state?.active_graph_version ?? 0}</strong></span><span>Documents<strong>{formatNumber(overview?.search_state?.document_count)}</strong></span></div>
            <div className="dq-table-wrap"><table className="dq-table mg-table"><thead><tr><th>Case</th><th>Query</th><th>Status</th><th>Results</th><th>Cold</th><th>Warm p95</th><th>Graph versions</th></tr></thead><tbody>{(benchmark.case_summaries || []).map((item, index) => <tr key={`${item.case_type}:${index}`} onClick={() => setSelected(item)}><td>{item.case_type}</td><td>{item.query}</td><td><StatusBadge value={item.status} /></td><td>{item.result_count ?? "-"}</td><td>{item.cold_latency_ms ?? "-"} ms</td><td>{item.warm_p95_ms ?? "-"} ms</td><td>{(item.graph_versions || []).join(", ") || "legacy"}</td></tr>)}</tbody></table></div>
          </div>
        </section>}

        {!!exports.length && tab === "validation" && <section className="dq-card"><div className="dq-card-head compact"><div><h3>Review issues</h3><p className="muted">Decide what becomes trusted, what stays quarantined, what gets removed, and what must be repaired.</p></div><div className="dq-card-actions"><button onClick={() => runAction("refresh-policy")}>Recalculate readiness</button></div></div>
          <div className="mg-resolution-buckets" aria-label="Issue resolution buckets">
            {ISSUE_BUCKETS.map((bucket) => {
              const count = bucket.policy && bucket.status
                ? queuePolicySummary[`${bucket.status}:${bucket.policy}`]
                : bucket.policy
                  ? queuePolicySummary[bucket.policy]
                  : queueSummary[bucket.status];
              return (
                <button type="button" key={bucket.id} className={issueBucket === bucket.id ? "active" : ""} onClick={() => { setIssueBucket(bucket.id); setSelected(null); }}>
                  <strong>{bucket.label}</strong>
                  <span>{formatNumber(count || 0)}</span>
                  <small>{bucket.description}</small>
                </button>
              );
            })}
          </div>
          <div className="mg-resolution-help">
            <strong>{currentBucket.label}</strong>
            <span>{currentBucket.description}</span>
          </div>
          <div className="mg-review-controls"><label>Reviewer<input value={reviewer} onChange={(event) => setReviewer(event.target.value)} /></label><label>Decision rationale<input value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} /></label></div>
          {selected?.issue_id && <div className="mg-issue-resolution-panel">
            <div>
              <span className="next-overline">Selected issue</span>
              <h3>{issueTitle(selected)}</h3>
              <p>The decision workspace is open on the right. Answer the agent question there, inspect evidence, then accept, quarantine, remove, repair, or block.</p>
              <div className="mg-issue-meta">
                <StatusBadge value={selected.publish_policy} />
                <StatusBadge value={selected.queue_status} />
                <span>Confidence {selected.agent_confidence == null ? `${Math.round(Number(selected.confidence || 0) * 100)}%` : `${Math.round(Number(selected.agent_confidence) * 100)}%`}</span>
              </div>
            </div>
          </div>}
          <div className="dq-table-wrap"><table className="dq-table mg-table mg-review-table"><thead><tr><th>Risk</th><th>Issue</th><th>Current / suggested</th><th>Confidence</th><th>Affected item</th><th>Actions</th></tr></thead><tbody>{queue.items.map((item) => <tr key={item.issue_id} onClick={() => setSelected(item)}><td><StatusBadge value={item.severity === "ERROR" ? "hard_block" : item.severity} /><small>{item.queue_status}</small></td><td><strong>{issueTitle(item)}</strong><small>{issueImpact(item)}</small><p className="mg-quiet-text">{shortText(item.agent_question || item.human_question || item.rationale, 110) || "Click to answer the agent question."}</p></td><td><div className="mg-policy-stack"><StatusBadge value={item.publish_policy || "not decided"} /><small>Agent: {shortText(recommendation(item), 56)}</small></div></td><td>{percent(item.agent_confidence ?? item.confidence)}</td><td><code title={affectedIdentity(item)}>{shortText(affectedIdentity(item), 58)}</code></td><td onClick={(event) => event.stopPropagation()}>{item.queue_status === "pending" && <div className="mg-row-actions"><button onClick={() => decide(item, "accept")}>Accept</button><button onClick={() => decide(item, "quarantine")}>Quarantine</button><button onClick={() => decide(item, "repair")}>Repair</button></div>}<button className="mg-row-open" onClick={() => setSelected(item)}>Open</button></td></tr>)}</tbody></table></div>
        </section>}

        {tab === "candidate" && <section className="dq-grid-2"><div className="dq-card"><div className="dq-card-head compact"><h3>Trusted candidate graph</h3><StatusBadge value={overview?.publish_report?.status || "dry_run"} /></div><div className="mg-facts"><span>Objects<strong>{formatNumber(objectCounts.trusted)}</strong></span><span>Relationships<strong>{formatNumber(relationshipCounts.trusted)}</strong></span><span>Invalid endpoints<strong>0</strong></span><span>Graph version<strong>{overview?.search_state?.active_graph_version ?? 0}</strong></span></div><button className="primary xl" onClick={() => runAction("candidate-dry-run")}>Validate candidate projection</button></div>
          <div className="dq-card"><div className="dq-card-head compact"><h3>Quarantine projection</h3><span>{formatNumber(quarantine.total)}</span></div><p>Retained for governance and GraphRAG, excluded from default search and traversal.</p><button onClick={() => runAction("enforce-trusted-graph")}>Enforce trusted-only candidate</button><div className="mg-compact-list">{quarantine.items.slice(0, 12).map((item) => <button key={`${item.node_id}:${item.object_type}`} onClick={() => setSelected(item)}><strong>{item.node_id}</strong><span>{item.object_type}</span><small>{item.publication_reason}</small></button>)}</div></div>
        </section>}

        {tab === "benchmark" && <section className="dq-card"><div className="dq-card-head compact"><div><h3>Fast Search Benchmark</h3><p className="muted">Cold p95 &lt; 1s, cached p95 &lt; 150ms, equivalent response shapes.</p></div><div className="dq-card-actions"><StatusBadge value={benchmark.status} /><button onClick={() => runAction("activate-candidate-search")}>Refresh candidate index</button><button onClick={() => runAction("benchmark")}>Run benchmark</button></div></div>
          <div className="mg-facts mg-benchmark-facts"><span>Cold p95<strong>{benchmark.acceptance?.cold_p95_ms ?? "-"} ms</strong></span><span>Warm p95<strong>{benchmark.acceptance?.warm_p95_ms ?? "-"} ms</strong></span><span>Graph version<strong>{overview?.search_state?.active_graph_version ?? 0}</strong></span><span>Documents<strong>{formatNumber(overview?.search_state?.document_count)}</strong></span></div>
          <div className="dq-table-wrap"><table className="dq-table mg-table"><thead><tr><th>Case</th><th>Query</th><th>Status</th><th>Results</th><th>Cold</th><th>Warm p95</th><th>Graph versions</th></tr></thead><tbody>{(benchmark.case_summaries || []).map((item, index) => <tr key={`${item.case_type}:${index}`} onClick={() => setSelected(item)}><td>{item.case_type}</td><td>{item.query}</td><td><StatusBadge value={item.status} /></td><td>{item.result_count ?? "-"}</td><td>{item.cold_latency_ms ?? "-"} ms</td><td>{item.warm_p95_ms ?? "-"} ms</td><td>{(item.graph_versions || []).join(", ") || "legacy"}</td></tr>)}</tbody></table></div>
        </section>}

        {!!exports.length && tab === "publish" && <section className="dq-grid-2"><div className="dq-card"><div className="dq-card-head compact"><h3>Release readiness</h3><StatusBadge value={publishReport.status || publication.status} /></div><div className="mg-blocker-list">{publishBlockers.map((item, index) => <button key={index} onClick={() => setSelected(typeof item === "object" ? item : { blocker: item })}><StatusBadge value="blocked" /><span>{typeof item === "object" ? item.reason || item.message : item}</span></button>)}</div>{!publishBlockers.length && <p className="muted">No blocking release gate is currently reported.</p>}{publishDisabledReason && <div className="mg-disabled-reason">Publish disabled: {publishDisabledReason}.</div>}</div>
          <div className="dq-card"><h3>Controlled activation</h3><p>Dry-run checks the release without changing the active graph. Publish promotes the trusted graph only after explicit approval.</p><label>Approver<input value={reviewer} onChange={(event) => setReviewer(event.target.value)} /></label><div className="mg-action-stack"><button onClick={() => runAction("publish-dry-run")}>Run publish dry-run</button><button className="primary" disabled={busy || !!publishDisabledReason} onClick={() => runAction("publish")}>Publish trusted graph</button></div></div>
        </section>}

        {!!exports.length && tab === "agents" && <section className="dq-grid-2"><div className="dq-card"><div className="dq-card-head compact"><h3>Agent runs</h3><span>{activity.agent_runs.length}</span></div><div className="mg-timeline">{activity.agent_runs.map((item) => <button key={item.id} onClick={() => setSelected(item)}><StatusBadge value={item.status} /><div><strong>{item.agent_name}</strong><span>{item.mode} | {item.proposal_count} proposals</span><small>{formatDate(item.started_at)}</small></div></button>)}</div></div>
          <div className="dq-card mg-eval-card"><div className="dq-card-head compact"><div><h3>Agent evaluation</h3><p className="muted">Scores validation proposals against approved queue decisions.</p></div><div className="dq-card-actions"><StatusBadge value={evaluations.latest_run?.status || "not run"} /><button onClick={() => runAction("evaluate-agent")}>Run evaluation</button></div></div>
            {!evaluations.latest_run && <p className="muted">No evaluation run yet. Approve a few queue decisions, then run evaluation to grade the agent.</p>}
            {evaluations.latest_run && <><div className="mg-eval-metrics">
              <span>Cases<strong>{formatNumber(evaluations.latest_run.case_count)}</strong></span>
              <span>Policy accuracy<strong>{percent(evaluations.latest_run.policy_accuracy)}</strong></span>
              <span>Unsafe accepts<strong>{formatNumber(evaluations.latest_run.unsafe_accept_count)}</strong></span>
              <span>Blocker recall<strong>{percent(evaluations.latest_run.blocker_recall)}</strong></span>
              <span>Useful question<strong>{percent(evaluations.latest_run.question_present_rate)}</strong></span>
              <span>Evidence plan<strong>{percent(evaluations.latest_run.summary?.average_evidence_plan_score)}</strong></span>
              <span>Safe queries<strong>{percent(evaluations.latest_run.summary?.average_query_intent_score)}</strong></span>
              <span>Avg confidence<strong>{percent(evaluations.latest_run.average_confidence)}</strong></span>
            </div>
            <div className="mg-eval-failures">
              {(evaluations.scores || []).slice(0, 8).map((item) => <button key={item.id || item.case_id} onClick={() => setSelected(item)} className={item.unsafe_accept ? "danger" : item.policy_exact ? "ok" : ""}>
                <StatusBadge value={item.policy_exact ? "matched" : item.unsafe_accept ? "unsafe_accept" : "mismatch"} />
                <strong>{item.issue_type || item.issue_id}</strong>
                <span>Expected {item.expected_policy} / got {item.proposed_policy}</span>
              </button>)}
            </div></>}
          </div>
          <div className="dq-card"><div className="dq-card-head compact"><h3>Approval interrupts</h3><span>{activity.approvals.length}</span></div><div className="mg-timeline">{activity.approvals.map((item) => <button key={item.approval_id} onClick={() => setSelected(item)}><StatusBadge value={item.status} /><div><strong>{item.gate_name}</strong><span>{item.question}</span><small>{formatDate(item.requested_at)}</small></div></button>)}</div></div>
          <div className="dq-card span-2"><div className="dq-card-head compact"><h3>Allowlisted tool executions</h3><span>{activity.tool_executions.length}</span></div><div className="dq-table-wrap"><table className="dq-table mg-table"><thead><tr><th>Tool</th><th>Agent</th><th>Status</th><th>Version</th><th>Input hash</th><th>Started</th><th>Completed</th></tr></thead><tbody>{activity.tool_executions.map((item) => <tr key={item.execution_id} onClick={() => setSelected(item)}><td>{item.tool_name}</td><td>{item.agent_name || "orchestrator"}</td><td><StatusBadge value={item.status} /></td><td>{item.tool_version}</td><td><code>{String(item.input_hash).slice(0, 12)}</code></td><td>{formatDate(item.started_at)}</td><td>{formatDate(item.completed_at)}</td></tr>)}</tbody></table></div></div>
          <div className="dq-card"><h3>Governance GraphRAG</h3><p className="muted">Ask why an item is blocked, quarantined, mapped, or published.</p><textarea rows={5} value={question} onChange={(event) => setQuestion(event.target.value)} /><button className="primary xl" onClick={askGraphRag}>Retrieve cited explanation</button></div>
          <div className="dq-card"><h3>Evidence-backed answer</h3>{!answer && <p className="muted">No question asked yet.</p>}{answer && <><div className="agent-answer">{answer.answer}</div><div className="mg-citations">{(answer.citations || []).map((item) => <button key={item.event_id} onClick={() => setSelected(item)}><strong>{item.event_type}</strong><span>{item.subject_id}</span></button>)}</div></>}</div>
        </section>}
        <EvidenceInspector
          item={selected}
          onClose={() => setSelected(null)}
          reviewNote={reviewNote}
          setReviewNote={setReviewNote}
          onAsk={askIssueAssistant}
          onResolve={resolveIssue}
        />
        {assistantOpen && <aside className="mg-assistant-panel" aria-label="Migration governance assistant">
          <div className="mg-assistant-head">
            <div><span className="next-overline">Plain-language guide</span><h3>Governance assistant</h3><p>{exportId} | screen: {NAV.find(([id]) => id === tab)?.[1] || tab}</p></div>
            <button onClick={() => setAssistantOpen(false)} aria-label="Close governance assistant">Close</button>
          </div>
          <div className="mg-assistant-messages">
            {assistantMessages.map((item, index) => <div key={`${item.role}:${index}`} className={`mg-assistant-message ${item.role}`}>
              <div>{String(item.content || "").split("\n").map((line, lineIndex) => <p key={lineIndex}>{line}</p>)}</div>
              {item.mode && <small>{item.mode}</small>}
              {!!item.citations?.length && <div className="mg-assistant-citations">{item.citations.slice(0, 5).map((citation) => <button key={citation.id || citation.subject || citation.label} onClick={() => setSelected(citation)}><strong>{citation.label || citation.type}</strong><span>{citation.type}</span></button>)}</div>}
            </div>)}
          </div>
          <div className="mg-assistant-suggestions">
            {assistantSuggestions.map((suggestion) => <button key={suggestion} onClick={() => askAssistant(suggestion)} disabled={assistantBusy}>{suggestion}</button>)}
          </div>
          <form className="mg-assistant-compose" onSubmit={(event) => { event.preventDefault(); askAssistant(); }}>
            <textarea value={assistantInput} onChange={(event) => setAssistantInput(event.target.value)} placeholder="Ask: what does quarantine mean, why is this blocked, what should I do next..." rows={3} />
            <button className="primary" disabled={assistantBusy || !assistantInput.trim()}><Send size={15} />{assistantBusy ? "Reading..." : "Ask"}</button>
          </form>
        </aside>}
      </main>
    </div>
  );
}
