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

  return (
    <aside className="plex-metadata-panel">
      <header>
        <span>
          <small>Card metadata</small>
          <strong>{node ? node.label : "No card selected"}</strong>
        </span>
      </header>
      <div className="plex-metadata-table-wrap">
        <table>
          <tbody>
            {!node && (
              <tr>
                <td colSpan={2}>Click a lineage card to inspect its metadata.</td>
              </tr>
            )}
            {rows.map(([key, value]) => (
              <tr key={key}>
                <th>{key}</th>
                <td title={value}>{value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </aside>
  );
}
