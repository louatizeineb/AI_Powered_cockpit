import type { LineageNode } from "../types/lineage.types";

type LineageControlsTableProps = {
  node?: LineageNode | null;
};

function asArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Record<string, unknown>[] : [];
}

function text(value: unknown, fallback = "-") {
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
}

function score(value: unknown) {
  if (value === undefined || value === null || value === "") return "-";
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return `${Math.round(numeric * 10) / 10}%`;
  return String(value);
}

export default function LineageControlsTable({ node }: LineageControlsTableProps) {
  const controls = asArray(node?.properties?.quality_checks);

  return (
    <section className="plex-controls-table">
      <header>
        <div>
          <strong>Selected asset controls</strong>
          <span>{node ? node.label : "No focused entity"}</span>
        </div>
        <small>{controls.length} controls</small>
      </header>

      <div className="plex-controls-scroll">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Control</th>
              <th>Status</th>
              <th>Score</th>
              <th>Field</th>
              <th>Object</th>
            </tr>
          </thead>
          <tbody>
            {controls.length === 0 && (
              <tr>
                <td colSpan={6}>No quality controls are attached to this entity yet.</td>
              </tr>
            )}
            {controls.map((control, index) => (
              <tr key={text(control.check_id || control.id, `control-${index}`)}>
                <td>{text(control.control_source || control.source, "DQC")}</td>
                <td>
                  <strong>{text(control.control_name || control.quality_dimension, "Quality control")}</strong>
                  <small>{text(control.quality_dimension || control.control_tool, "")}</small>
                </td>
                <td>
                  <span className="plex-control-status">
                    {text(control.control_status || control.status || control.confidence_level)}
                  </span>
                </td>
                <td>{score(control.score || control.quality_score || control.control_score)}</td>
                <td>{text(control.field || control.controlled_field_name)}</td>
                <td>{text(control.controlled_object_name || control.controlled_structure_name || control.matched_path_full)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
