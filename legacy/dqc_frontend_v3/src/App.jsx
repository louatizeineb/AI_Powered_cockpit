import React, { useMemo, useState } from "react";
import LineageExplorer from "./components/LineageExplorer";
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

function DqcWorkspace() {
  const [activeTab, setActiveTab] = useState("entry");
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
      setLastRun({ type: "file", fileName: file.name, status: "backend-error-or-timeout", error: err.message });
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
    <div className="dq-shell">
      <aside className="dq-sidebar">
        <div className="dq-brand">
          <div className="dq-brand-mark">DQ</div>
          <div>
            <h1>Quality Cockpit</h1>
            <p>DQC Resolution • GraphRAG • Human Review</p>
          </div>
        </div>

        <nav className="dq-nav">
          {[
            ["entry", "1. Upload / Connect"],
            ["logs", "2. Run Logs"],
            ["resolved", "3. Resolved Results"],
            ["review", "4. Human Review"],
            ["dlq", "5. DLQ / Unresolved"],
            ["agent", "6. Agent Investigation"],
          ].map(([id, label]) => (
            <button
              key={id}
              className={activeTab === id ? "active" : ""}
              onClick={() => {
                setActiveTab(id);
                if (id !== "entry") refreshAll();
              }}
            >
              {label}
            </button>
          ))}
        </nav>

        <div className="dq-mini-panel">
          <button className="primary" onClick={handleResetWorkspace} disabled={busy}>
            {busy ? "Resetting..." : "Reset demo workspace"}
          </button>
          <button onClick={handleRunDemoWorkflow} disabled={busy}>
            Run agent smoke workflow
          </button>
        </div>

        {error && <div className="dq-error">{error}</div>}
      </aside>

      <main className="dq-main">
        <header className="dq-hero">
          <div>
            <span className="eyebrow">AI-Powered Data Quality Cockpit</span>
            <h2>Resolve quality checks against your catalog graph</h2>
            <p>
              Upload DQC results or connect a quality-check table. The backend validates,
              normalizes, matches, embeds, investigates with the agent, and sends uncertain
              matches to human review.
            </p>
          </div>
          <div className="dq-kpi-grid">
            <MetricCard label="Resolved" value={kpis.total} hint="latest loaded" />
            <MetricCard label="High confidence" value={kpis.high} tone="good" />
            <MetricCard label="Needs review" value={kpis.medium} tone="warn" />
            <MetricCard label="Unresolved" value={kpis.unresolved} tone="bad" />
          </div>
        </header>

        {activeTab === "entry" && (
          <section className="dq-grid-2">
            <div className="dq-card big">
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
              className={cls("dq-dropzone", dragging && "dragging")}
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
              <div className="drop-icon">⬡</div>
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

            <div className="dq-card span-2">
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
              <div className="dq-card span-2">
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
              <button onClick={refreshAll}>Refresh</button>
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

        {selected && (
          <aside className="dq-inspector">
            <div className="dq-card-head compact">
              <h3>Inspector</h3>
              <button onClick={() => setSelected(null)}>Close</button>
            </div>
            <JsonBlock data={selected} />
          </aside>
        )}
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
    <div className="app-root">
      <div className="top-switcher">
        <div>
          <strong>AI-Powered Data Quality Cockpit</strong>
          <span>Lineage + DQC Resolution + GraphRAG Agent</span>
        </div>
        <div className="switch-actions">
          <button className={mode === "dqc" ? "active" : ""} onClick={() => setMode("dqc")}>
            DQC Agent Cockpit
          </button>
          <button className={mode === "lineage" ? "active" : ""} onClick={() => setMode("lineage")}>
            Lineage Explorer
          </button>
        </div>
      </div>
      {mode === "dqc" ? <DqcWorkspace /> : <LineageExplorer />}
    </div>
  );
}
