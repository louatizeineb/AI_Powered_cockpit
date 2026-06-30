import { useMemo, useState } from "react";
import { Check, Copy, Search, X } from "lucide-react";

const GROUPS = [
  ["Decision", ["status", "confidence", "score", "policy", "decision", "rationale", "severity", "state"]],
  ["Identity", ["id", "name", "label", "path", "type", "application", "table", "column", "node"]],
  ["Evidence", []],
];

function valueText(value) {
  if (value === undefined || value === null || value === "") return "-";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function humanize(key) {
  return key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function groupFor(key) {
  const normalized = key.toLowerCase();
  return GROUPS.find(([, terms]) => terms.some((term) => normalized.includes(term)))?.[0] || "Evidence";
}

export default function RecordInspector({ item, title = "Record inspector", eyebrow = "Evidence", onClose }) {
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState("");
  const rows = useMemo(() => Object.entries(item || {}).filter(([key, value]) => {
    const needle = query.trim().toLowerCase();
    return !needle || key.toLowerCase().includes(needle) || valueText(value).toLowerCase().includes(needle);
  }), [item, query]);

  const grouped = useMemo(() => GROUPS.map(([name]) => [name, rows.filter(([key]) => groupFor(key) === name)]).filter(([, values]) => values.length), [rows]);

  async function copy(value, key) {
    await navigator.clipboard.writeText(valueText(value));
    setCopied(key);
    window.setTimeout(() => setCopied(""), 1400);
  }

  const subject = item?.label || item?.name || item?.controlled_structure_name || item?.issue_id || item?.id;

  return (
    <aside className="record-inspector" aria-label={title}>
      <header className="record-inspector-header">
        <div>
          <span>{eyebrow}</span>
          <h3>{title}</h3>
          {subject && <p>{String(subject)}</p>}
        </div>
        <div className="record-inspector-actions">
          <button type="button" title="Copy all properties" aria-label="Copy all properties" onClick={() => copy(item, "all")}>
            {copied === "all" ? <Check size={17} /> : <Copy size={17} />}
          </button>
          <button type="button" title="Close inspector" aria-label="Close inspector" onClick={onClose}><X size={18} /></button>
        </div>
      </header>

      <div className="record-inspector-search">
        <Search size={16} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter properties or values" aria-label="Filter inspector properties" />
        <span>{rows.length}</span>
      </div>

      <div className="record-inspector-content">
        {!rows.length && <div className="record-inspector-empty">No properties match this filter.</div>}
        {grouped.map(([group, values]) => (
          <section key={group} className="record-property-group">
            <div className="record-property-group-title"><h4>{group}</h4><span>{values.length}</span></div>
            <dl>
              {values.map(([key, value]) => (
                <div key={key} className="record-property-row">
                  <dt title={key}>{humanize(key)}</dt>
                  <dd><span>{valueText(value)}</span><button type="button" title={`Copy ${humanize(key)}`} aria-label={`Copy ${humanize(key)}`} onClick={() => copy(value, key)}>{copied === key ? <Check size={14} /> : <Copy size={14} />}</button></dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </aside>
  );
}
