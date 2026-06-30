import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, AlertTriangle, Bot, ClipboardPaste, Database, GitBranch,
  LayoutDashboard, ListChecks, Network, Play, RefreshCcw,
  SearchCheck, ShieldCheck, Upload,
} from "lucide-react";
import LineageExplorer from "./features/lineage/components/LineageExplorer";
import MigrationGovernance from "./features/migration/MigrationGovernance";
import RecordInspector from "./components/RecordInspector";
import {
  connectDqcDatabase,
  uploadDqcFile,
  resetDqcWorkspace,
  fetchResolvedDqc,
  fetchUnresolvedDqc,
  fetchPipelineLogs,
  approveDqcMatch,
  rejectDqcMatch,
  askDqcAgent,
  runDqcAgentWorkflow,
} from "./api";
import "./styles.css";

const DEMO_EVENT = {
  applicationcode: "MKD",
  controlledobjectname: "[s_comp_company.n_ident_compy]",
  controlledobjecttype: "Table",
  controlledsourcename: "s_comp_company",
  businesstermname: null,
  controlname: "Frontend smoke completeness check",
  qualitydimension: "Completeness",
  acceptancethreshold: 95,
  executiontimestamp: "2026-05-21T10:00:00",
  businessdate: "2026-05-21",
  controlleditemcount: 10,
  okcount: 10,
  kocount: 0,
  controltool: "FRONTEND_DEMO",
  cdqprofile: "frontend-demo-profile",
  controllink: null,
};

function cls(...items) {
  return items.filter(Boolean).join(" ");
}

function safeItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.data?.items)) return payload.data.items;
  return [];
}

function ConfidenceBadge({ value }) {
  const normalized = String(value || "UNKNOWN").toLowerCase();
  return <span className={cls("dq-badge", normalized)}>{value || "UNKNOWN"}</span>;
}

function ControlStatusBadge({ value }) {
  const normalized = String(value || "UNKNOWN").toLowerCase();
  const label = value === "NO_THRESHOLD" ? "NO THRESHOLD" : value || "UNKNOWN";
  return <span className={cls("dq-badge", "control", normalized)}>{label}</span>;
}

function MetricCard({ label, value, hint, tone = "" }) {
  return (
    <div className={cls("metric-card", tone)}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </div>
  );
}

function JsonBlock({ data }) {
  return <pre className="json-block">{JSON.stringify(data, null, 2)}</pre>;
}

function csvCell(value) {
  if (value === undefined || value === null) return "";
  const text = Array.isArray(value) || typeof value === "object" ? JSON.stringify(value) : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

function exportRowsToCsv(rows, filename) {
  if (!rows.length) return;
  const columns = [
    ["id", "ID"],
    ["application_code_norm", "App"],
    ["controlled_structure_name", "Controlled structure"],
    ["controlled_field_name", "Controlled field"],
    ["controlled_object_name_raw", "Controlled object raw"],
    ["matched_entity_level", "Matched level"],
    ["matched_node_id", "Matched node id"],
    ["matched_path_full", "Matched path"],
    ["match_method", "Method"],
    ["match_score", "Match score"],
    ["confidence_level", "Confidence"],
    ["control_status", "Control status"],
    ["control_score", "Control score"],
    ["quality_score", "Quality score"],
    ["acceptance_threshold", "Acceptance threshold"],
    ["ok_count", "OK count"],
    ["ko_count", "KO count"],
    ["controlled_item_count", "Controlled item count"],
    ["control_name", "Control name"],
    ["quality_dimension", "Quality dimension"],
    ["control_tool", "Control tool"],
    ["control_link", "Control link"],
    ["reviewed", "Reviewed"],
    ["human_review_required", "Human review required"],
    ["resolution_status", "Resolution status"],
  ];
  const csv = [
    columns.map(([, label]) => csvCell(label)).join(","),
    ...rows.map((row) => columns.map(([key]) => csvCell(row[key])).join(",")),
  ].join("\r\n");
  const blob = new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function DqcWorkspace() {
  const [activeTab, setActiveTab] = useState("entry");
  const [entryMethod, setEntryMethod] = useState("database");
  const [tableName, setTableName] = useState("DQC");
  const [limit, setLimit] = useState(1000);
  const [dragging, setDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [csvPaste, setCsvPaste] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastRun, setLastRun] = useState(null);
  const [resolved, setResolved] = useState([]);
  const [unresolved, setUnresolved] = useState([]);
  const [logs, setLogs] = useState([]);
  const [agentPrompt, setAgentPrompt] = useState("Analyze unresolved DQC events and group them by failure reason. Explain what should be fixed first.");
  const [agentResponse, setAgentResponse] = useState(null);
  const [selected, setSelected] = useState(null);
  const [reviewNote, setReviewNote] = useState("Reviewed and accepted for demo.");
  const [error, setError] = useState("");
  const inspectorRef = useRef(null);

  useEffect(() => {
    function handleOutsideClick(event) {
      if (!selected || !inspectorRef.current) return;
      if (!inspectorRef.current.contains(event.target)) setSelected(null);
    }

    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [selected]);

  const reviewQueue = useMemo(
    () => resolved.filter((item) => item.human_review_required && !item.reviewed),
    [resolved]
  );

  const kpis = useMemo(() => {
    const high = resolved.filter((x) => x.confidence_level === "HIGH").length;
    const medium = resolved.filter((x) => x.confidence_level === "MEDIUM").length;
    const reviewed = resolved.filter((x) => x.reviewed).length;
    return { high, medium, reviewed, unresolved: unresolved.length, total: resolved.length };
  }, [resolved, unresolved]);

  async function refreshAll() {
    setBusy(true);
    setError("");

    const [resolvedData, unresolvedData, logData] = await Promise.allSettled([
      fetchResolvedDqc(100),
      fetchUnresolvedDqc(100),
      fetchPipelineLogs(100),
    ]);

    const errors = [];
    if (resolvedData.status === "fulfilled") setResolved(safeItems(resolvedData.value));
    else errors.push(`resolved: ${resolvedData.reason?.message || "failed"}`);

    if (unresolvedData.status === "fulfilled") setUnresolved(safeItems(unresolvedData.value));
    else errors.push(`unresolved: ${unresolvedData.reason?.message || "failed"}`);

    if (logData.status === "fulfilled") setLogs(safeItems(logData.value));
    else errors.push(`logs: ${logData.reason?.message || "failed"}`);

    if (errors.length) setError(`Refresh partially failed: ${errors.join(" | ")}`);
    setBusy(false);
  }

  function handleExportResolvedCsv() {
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    exportRowsToCsv(resolved, `dqc-matching-results-${stamp}.csv`);
  }

  async function handleResetWorkspace() {
    setBusy(true);
    setError("");
    try {
      const data = await resetDqcWorkspace();
      setLastRun({ type: "reset-workspace", data });
      setResolved([]);
      setUnresolved([]);
      setLogs([]);
      setAgentResponse(null);
      setSelected(null);
      setActiveTab("entry");
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Workspace reset failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleConnect() {
    setBusy(true);
    setError("");
    try {
      const data = await connectDqcDatabase({ tableName, limit });
      setLastRun({ type: "database", data });
      await refreshAll();
      setActiveTab("resolved");
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Database connection failed");
    } finally {
      setBusy(false);
    }
  }

  function isSupportedDqcFile(file) {
    const name = String(file?.name || "").toLowerCase();
    return [".csv", ".json", ".jsonl", ".parquet", ".pq"].some((ext) => name.endsWith(ext));
  }

  async function handleFile(file) {
    if (!file) return;
    if (!isSupportedDqcFile(file)) {
      setError("Unsupported file. Use CSV, JSON, JSONL, Parquet, or PQ.");
      return;
    }

    setBusy(true);
    setError("");
    setLastRun({ type: "file", fileName: file.name, status: "uploading" });

    try {
      const data = await uploadDqcFile(file);
      setLastRun({ type: "file", fileName: file.name, data });
      setActiveTab("resolved");
    } catch (err) {
      setLastRun({ type: "file", fileName: file.name, status: "backend-error", error: err.message });
      setError(err.message || "Upload failed. The backend may still have processed the file; refreshing results now.");
    } finally {
      await refreshAll();
      setBusy(false);
      setDragging(false);
    }
  }

  async function handleSelectedFileRun() {
    if (!selectedFile) {
      setError("Choose or drag a DQC file first.");
      return;
    }
    await handleFile(selectedFile);
  }

  async function handlePastedCsvRun() {
    if (!csvPaste.trim()) {
      setError("Paste CSV content first.");
      return;
    }
    const blob = new Blob([csvPaste], { type: "text/csv;charset=utf-8" });
    const file = new File([blob], `pasted-dqc-${Date.now()}.csv`, { type: "text/csv" });
    await handleFile(file);
  }

  async function handleApprove(item) {
    setBusy(true);
    setError("");
    try {
      const data = await approveDqcMatch(item.id, {
        reviewer: "Zeineb",
        note: reviewNote || "Approved from frontend.",
      });
      setLastRun({ type: "approve", id: item.id, data });
      await refreshAll();
      setSelected(data);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Approve failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleReject(item) {
    setBusy(true);
    setError("");
    try {
      const data = await rejectDqcMatch(item.id, {
        reviewer: "Zeineb",
        reason: reviewNote || "Rejected from frontend review.",
      });
      setLastRun({ type: "reject", id: item.id, data });
      await refreshAll();
      setSelected(data);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Reject failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleAskAgent() {
    setBusy(true);
    setError("");
    try {
      const data = await askDqcAgent(agentPrompt);
      setAgentResponse(data);
      setActiveTab("agent");
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Agent request failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleRunDemoWorkflow() {
    setBusy(true);
    setError("");
    try {
      const data = await runDqcAgentWorkflow(DEMO_EVENT, true);
      setLastRun({ type: "agent-workflow", data });
      setAgentResponse(data);
      await refreshAll();
      setActiveTab("agent");
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || "Agent workflow failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dq-shell next-workspace">
      <aside className="dq-sidebar next-context-nav">
        <div className="dq-brand next-context-heading">
          <div className="dq-brand-mark next-context-icon"><ShieldCheck size={19} /></div>
          <div>
            <h1>Quality operations</h1>
            <p>Resolve and review controls</p>
          </div>
        </div>

        <nav className="dq-nav">
          {[
            ["entry", "New run", Play, null],
            ["resolved", "Results", SearchCheck, kpis.total],
            ["review", "Review inbox", ListChecks, reviewQueue.length],
            ["dlq", "Unresolved", AlertTriangle, kpis.unresolved],
            ["logs", "Activity", Activity, null],
            ["agent", "AI analyst", Bot, null],
          ].map(([id, label, Icon, count]) => (
            <button
              key={id}
              className={activeTab === id ? "active" : ""}
              onClick={() => {
                setActiveTab(id);
                if (id !== "entry") refreshAll();
              }}
            >
              <Icon size={17} />
              <span>{label}</span>
              {count !== null && <small>{count}</small>}
            </button>
          ))}
        </nav>

        <div className="dq-mini-panel next-context-footer">
          <span>Workspace tools</span>
          <button onClick={handleRunDemoWorkflow} disabled={busy}><Bot size={15} /> Agent smoke test</button>
          <button onClick={handleResetWorkspace} disabled={busy}><RefreshCcw size={15} /> Reset workspace</button>
        </div>

        {error && <div className="dq-error">{error}</div>}
      </aside>

      <main className="dq-main next-workspace-main">
        <header className="dq-workspace-header">
          <div>
            <span className="next-overline">Data quality</span>
            <h2>{activeTab === "entry" ? "Start a quality run" : activeTab === "review" ? "Review inbox" : activeTab === "agent" ? "AI quality analyst" : activeTab === "dlq" ? "Unresolved controls" : activeTab === "logs" ? "Run activity" : "Resolved controls"}</h2>
            <p>
              {activeTab === "entry" ? "Bring in control results. Matching, validation, and routing happen automatically." : "Work from evidence, resolve exceptions, and keep the catalog trustworthy."}
            </p>
          </div>
          <div className="dq-header-kpis">
            <MetricCard label="Resolved" value={kpis.total} />
            <MetricCard label="Needs review" value={reviewQueue.length} tone="warn" />
            <MetricCard label="Unresolved" value={kpis.unresolved} tone="bad" />
          </div>
        </header>

        {activeTab === "entry" && (
          <section className="dq-intake-layout">
            <div className="dq-method-switch" role="tablist" aria-label="Quality data source">
              <button className={entryMethod === "database" ? "active" : ""} onClick={() => setEntryMethod("database")}><Database size={17} /> Database</button>
              <button className={entryMethod === "file" ? "active" : ""} onClick={() => setEntryMethod("file")}><Upload size={17} /> File</button>
              <button className={entryMethod === "paste" ? "active" : ""} onClick={() => setEntryMethod("paste")}><ClipboardPaste size={17} /> Paste data</button>
            </div>
            <div className={cls("dq-card", "big", "dq-intake-card", entryMethod !== "database" && "next-hidden")}>
              <div className="dq-card-head">
                <span className="step">A</span>
                <div>
                  <h3>Connect quality-check database/table</h3>
                  <p>Use your existing PostgreSQL DQC table and launch resolution immediately.</p>
                </div>
              </div>
              <label>Table name</label>
              <input value={tableName} onChange={(e) => setTableName(e.target.value)} />
              <label>Limit for demo run</label>
              <input type="number" value={limit} onChange={(e) => setLimit(e.target.value)} />
              <button className="primary xl" onClick={handleConnect} disabled={busy}>
                {busy ? "Launching resolution..." : "Connect database and run DQC resolution"}
              </button>
            </div>

            <div
              className={cls("dq-dropzone", "dq-intake-card", dragging && "dragging", entryMethod !== "file" && "next-hidden")}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                handleFile(e.dataTransfer.files?.[0]);
              }}
            >
              <div className="drop-icon"><Upload size={28} /></div>
              <h3>Drag and drop quality-check file</h3>
              <p>CSV, JSON, JSONL, or Parquet. Resolution starts automatically.</p>
              <input
                id="dqc-file"
                type="file"
                accept=".csv,.json,.jsonl,.parquet,.pq,text/csv,application/json,application/x-parquet,application/octet-stream"
                onChange={(e) => {
                  const file = e.target.files?.[0] || null;
                  setSelectedFile(file);
                  if (file) handleFile(file);
                }}
                hidden
              />
              <label className="file-button" htmlFor="dqc-file">Choose file and run</label>
              {selectedFile && <small className="muted">Selected: {selectedFile.name}</small>}
              <button className="secondary-wide" onClick={handleSelectedFileRun} disabled={busy || !selectedFile}>
                Run selected file
              </button>
            </div>

            <div className={cls("dq-card", "dq-intake-card", entryMethod !== "paste" && "next-hidden")}>
              <div className="dq-card-head compact">
                <div>
                  <h3>Paste CSV directly</h3>
                  <p className="muted">Paste DQC rows with headers. The frontend creates a temporary CSV file and launches the same upload pipeline.</p>
                </div>
              </div>
              <textarea
                rows={8}
                value={csvPaste}
                onChange={(e) => setCsvPaste(e.target.value)}
                placeholder={'applicationcode,controlledobjectname,controlledsourcename,controlleditemcount,okcount,kocount\nMKD,[s_comp_company.n_ident_compy],s_comp_company,10,10,0'}
              />
              <button className="primary xl" onClick={handlePastedCsvRun} disabled={busy || !csvPaste.trim()}>
                {busy ? "Processing pasted CSV..." : "Run pasted CSV through DQC resolution"}
              </button>
            </div>

            {lastRun && (
              <div className="dq-card dq-last-run">
                <h3>Latest run result</h3>
                <JsonBlock data={lastRun} />
              </div>
            )}
          </section>
        )}

        {activeTab === "logs" && (
          <section className="dq-card">
            <div className="dq-card-head compact">
              <h3>Resolution Run Logs</h3>
              <button onClick={refreshAll}>Refresh</button>
            </div>
            <div className="log-list">
              {logs.length === 0 && <p className="muted">No logs returned yet.</p>}
              {logs.map((log, i) => (
                <div className="log-row" key={log.id || i}>
                  <strong>{log.stage || log.event_type || log.level || "LOG"}</strong>
                  <span>{log.created_at || log.timestamp || ""}</span>
                  <p>{log.message || log.failure_reason || JSON.stringify(log).slice(0, 240)}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {activeTab === "resolved" && (
          <section className="dq-card">
            <div className="dq-card-head compact">
              <h3>Resolved Results</h3>
              <div className="dq-card-actions">
                <button onClick={handleExportResolvedCsv} disabled={!resolved.length}>Export CSV</button>
                <button onClick={refreshAll}>Refresh</button>
              </div>
            </div>
            <DqcTable
              items={resolved}
              onSelect={setSelected}
              actions={false}
            />
          </section>
        )}

        {activeTab === "review" && (
          <section className="dq-card">
            <div className="dq-card-head compact">
              <div>
                <h3>Human Review Queue</h3>
                <p className="muted">Medium-confidence matches need accept/reject validation.</p>
              </div>
              <button onClick={refreshAll}>Refresh</button>
            </div>

            <label>Review note / reason</label>
            <input value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} />

            <DqcTable
              items={reviewQueue}
              onSelect={setSelected}
              actions
              onApprove={handleApprove}
              onReject={handleReject}
            />
          </section>
        )}

        {activeTab === "dlq" && (
          <section className="dq-card">
            <div className="dq-card-head compact">
              <h3>DLQ / Unresolved</h3>
              <button onClick={refreshAll}>Refresh</button>
            </div>
            <div className="unresolved-grid">
              {unresolved.length === 0 && <p className="muted">No unresolved records returned.</p>}
              {unresolved.map((item) => (
                <button key={item.id} className="unresolved-card" onClick={() => setSelected(item)}>
                  <strong>{item.failure_reason || "UNRESOLVED"}</strong>
                  <span>{item.failure_stage || "MATCHING"}</span>
                  <small>
                    {item.failure_details?.normalized?.application_code_norm ||
                      item.normalized_payload?.application_code_norm ||
                      "unknown app"}
                  </small>
                </button>
              ))}
            </div>
          </section>
        )}

        {activeTab === "agent" && (
          <section className="dq-grid-2">
            <div className="dq-card">
              <h3>Agent Investigation Panel</h3>
              <p className="muted">
                Ask the fixed workflow agent to inspect resolved matches, DLQ records,
                candidates, and review actions.
              </p>
              <textarea
                rows={7}
                value={agentPrompt}
                onChange={(e) => setAgentPrompt(e.target.value)}
              />
              <button className="primary" onClick={handleAskAgent} disabled={busy}>
                {busy ? "Agent thinking..." : "Ask DQC agent"}
              </button>
            </div>
            <div className="dq-card">
              <h3>Agent answer</h3>
              {!agentResponse && <p className="muted">No agent answer yet.</p>}
              {agentResponse?.explanation && (
                <div className="agent-answer">{agentResponse.explanation}</div>
              )}
              {agentResponse && !agentResponse.explanation && <JsonBlock data={agentResponse} />}
              {agentResponse?.tool_used && (
                <div className="tool-pill">Tool used: {agentResponse.tool_used}</div>
              )}
            </div>
          </section>
        )}

        {selected && <div ref={inspectorRef}><RecordInspector item={selected} title="Quality record" eyebrow="Quality evidence" onClose={() => setSelected(null)} /></div>}
      </main>
    </div>
  );
}


function DqcTable({ items, onSelect, actions, onApprove, onReject }) {
  if (!items.length) return <p className="muted">No records returned.</p>;

  return (
    <div className="dq-table-wrap">
      <table className="dq-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>App</th>
            <th>Controlled object</th>
            <th>Matched level</th>
            <th>Method</th>
            <th>Score</th>
            <th>Confidence</th>
            <th>Control status</th>
            <th>Control score</th>
            {actions && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} onClick={() => onSelect(item)}>
              <td>{item.id}</td>
              <td>{item.application_code_norm}</td>
              <td>
                <strong>{item.controlled_structure_name}</strong>
                {item.controlled_field_name && <small>.{item.controlled_field_name}</small>}
                <em>{item.matched_path_full}</em>
              </td>
              <td>{item.matched_entity_level}</td>
              <td>{item.match_method}</td>
              <td>{item.match_score}</td>
              <td><ConfidenceBadge value={item.confidence_level} /></td>
              <td>
                <ControlStatusBadge value={item.control_status} />
                <small>{item.control_name || item.quality_dimension || ""}</small>
              </td>
              <td>
                {item.control_score ?? item.quality_score ?? "-"}%
                <small>
                  OK {item.ok_count ?? "-"} / {item.controlled_item_count ?? "-"} · threshold {item.acceptance_threshold ?? "-"}
                </small>
              </td>
              {actions && (
                <td className="table-actions" onClick={(e) => e.stopPropagation()}>
                  <button className="approve" onClick={() => onApprove(item)}>Approve</button>
                  <button className="reject" onClick={() => onReject(item)}>Reject</button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function App() {
  const [mode, setMode] = useState("dqc");

  return (
    <div className="app-root next-app">
      <header className="next-product-bar">
        <div className="next-product-brand">
          <div className="next-product-mark"><GitBranch size={19} /></div>
          <div><strong>Data Cockpit</strong></div>
        </div>
        <nav className="next-product-nav" aria-label="Product areas">
          <button aria-label="Quality" className={mode === "dqc" ? "active" : ""} onClick={() => setMode("dqc")}><ShieldCheck size={17} /><span>Quality</span></button>
          <button aria-label="Lineage" className={mode === "lineage" ? "active" : ""} onClick={() => setMode("lineage")}><Network size={17} /><span>Lineage</span></button>
          <button aria-label="Governance" className={mode === "migration" ? "active" : ""} onClick={() => setMode("migration")}><LayoutDashboard size={17} /><span>Governance</span></button>
        </nav>
        <div className="next-product-actions">
          <span className="next-environment"><i /> Local</span>
        </div>
      </header>
      {mode === "dqc" ? <DqcWorkspace /> : mode === "lineage" ? <LineageExplorer /> : <MigrationGovernance />}
    </div>
  );
}
