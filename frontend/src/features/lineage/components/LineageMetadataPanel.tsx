import { useMemo, useState } from "react";
import { Check, Copy, Search } from "lucide-react";
import type { LineageNode } from "../types/lineage.types";

type LineageMetadataPanelProps = {
  node?: LineageNode | null;
};

const IMPORTANT_KEYS = [
  "node_id",
  "label",
  "technical_name",
  "type",
  "category",
  "parent_label",
  "path",
  "name_label",
  "name_tech",
  "path_full",
  "usage_name",
  "usage_tech_name",
  "usage_path",
  "entity_type",
  "data_type",
  "catalog_label",
  "workspace_id",
  "parent_node_id",
  "parent_uuid",
  "app_code",
  "application_code",
  "created_at",
  "updated_at",
  "description",
  "dlk_url",
];

function stringify(value: unknown) {
  if (value === undefined || value === null || value === "") return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function metadataRows(node?: LineageNode | null) {
  if (!node) return [];
  const merged: Record<string, unknown> = {
    node_id: node.node_id,
    label: node.label,
    technical_name: node.technical_name,
    type: node.type,
    category: node.category,
    parent_label: node.parent_label,
    path: node.path,
    ...(node.properties || {}),
  };
  const used = new Set<string>();
  const rows = IMPORTANT_KEYS
    .map((key) => {
      used.add(key);
      return [key, stringify(merged[key])] as const;
    })
    .filter(([, value]) => value);

  Object.entries(merged)
    .filter(([key, value]) => !used.has(key) && !["quality_checks", "quality"].includes(key) && stringify(value))
    .sort(([left], [right]) => left.localeCompare(right))
    .slice(0, 35)
    .forEach(([key, value]) => rows.push([key, stringify(value)]));

  return rows;
}

export default function LineageMetadataPanel({ node }: LineageMetadataPanelProps) {
  const rows = metadataRows(node);
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState("");
  const filteredRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? rows.filter(([key, value]) => key.toLowerCase().includes(needle) || value.toLowerCase().includes(needle)) : rows;
  }, [rows, query]);

  async function copy(value: string, key: string) {
    await navigator.clipboard.writeText(value);
    setCopied(key);
    window.setTimeout(() => setCopied(""), 1400);
  }

  return (
    <aside className="plex-metadata-panel">
      <header>
        <span>
          <small>Entity inspector</small>
          <strong>{node ? node.label : "No card selected"}</strong>
          {node && <em>{node.type || node.category || "Metadata entity"}</em>}
        </span>
        {node && <button type="button" title="Copy all metadata" aria-label="Copy all metadata" onClick={() => copy(JSON.stringify(Object.fromEntries(rows), null, 2), "all")}>{copied === "all" ? <Check size={16} /> : <Copy size={16} />}</button>}
      </header>
      <div className="plex-metadata-body">
        {node && <div className="plex-metadata-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter metadata" aria-label="Filter entity metadata" /><span>{filteredRows.length}</span></div>}
        <div className="plex-metadata-table-wrap">
          {!node && <div className="plex-metadata-empty"><Search size={20} /><strong>Select an entity</strong><span>Click a lineage card to inspect its metadata and source evidence.</span></div>}
          {node && !filteredRows.length && <div className="plex-metadata-empty"><strong>No matching metadata</strong><span>Try a property name or value.</span></div>}
          {node && <dl className="plex-metadata-list">
            {filteredRows.map(([key, value]) => (
              <div key={key}>
                <dt title={key}>{key.replace(/_/g, " ")}</dt>
                <dd title={value}><span>{value}</span><button type="button" title={`Copy ${key}`} aria-label={`Copy ${key}`} onClick={() => copy(value, key)}>{copied === key ? <Check size={13} /> : <Copy size={13} />}</button></dd>
              </div>
            ))}
          </dl>}
        </div>
      </div>
    </aside>
  );
}
