import type { LineageNode } from "../types/lineage.types";

export type LineageQualityTone = "good" | "review" | "critical" | "neutral";
export type LineageQualityItem = Record<string, unknown>;
export type LineageQualityIndex = Map<string, LineageQualityItem[]>;

export type LineageQualityBadge = {
  tone: LineageQualityTone;
  label: string;
  count: number;
};

function text(value: unknown) {
  return String(value ?? "").trim();
}

export function normalizeQualityText(value: unknown) {
  return text(value).toLowerCase();
}

function compactKey(value: unknown) {
  return normalizeQualityText(value).replace(/[\s\\/>|_.:-]+/g, "");
}

function pushKey(keys: Set<string>, value: unknown, allowShort = false) {
  const normalized = normalizeQualityText(value);
  if (!normalized) return;
  if (allowShort || normalized.length >= 3) keys.add(normalized);
  const compacted = compactKey(value);
  if (compacted && (allowShort || compacted.length >= 3)) keys.add(compacted);
}

function propsOf(node: LineageNode) {
  return (node.properties || {}) as Record<string, unknown>;
}

export function qualityKeysForNode(node: LineageNode): string[] {
  const props = propsOf(node);
  const keys = new Set<string>();
  const app = props.app_code || props.application_code || props.application_code_norm;
  const structure =
    props.structure_name ||
    props.controlled_structure_name ||
    props.parent_name ||
    node.parent_label ||
    node.group_label;
  const field =
    props.field_name ||
    props.controlled_field_name ||
    props.column_name ||
    node.technical_name ||
    node.label;

  [
    node.id,
    node.node_id,
    node.path_full,
    node.path,
    node.technical_name,
    node.label,
    node.group_id,
    props.id,
    props.node_id,
    props.path_full,
    props.path,
    props.technical_path,
    props.qualified_name,
    props.app_code,
    props.application_code,
    props.application_code_norm,
    props.structure_name,
    props.controlled_structure_name,
    props.field_name,
    props.controlled_field_name,
    props.column_name,
  ].forEach((value) => pushKey(keys, value));

  [
    [app, structure].filter(Boolean).join("."),
    [app, structure, field].filter(Boolean).join("."),
    [structure, field].filter(Boolean).join("."),
    [node.path || node.path_full, field].filter(Boolean).join("."),
  ].forEach((value) => pushKey(keys, value));

  return [...keys];
}

export function qualityKeysForItem(item: LineageQualityItem): string[] {
  const keys = new Set<string>();
  const app = item.application_code_norm || item.application_code || item.app_code;
  const structure =
    item.controlled_structure_name ||
    item.structure_name ||
    item.table_name ||
    item.dataset_name ||
    item.asset_name;
  const field = item.controlled_field_name || item.field_name || item.column_name;

  [
    item.id,
    item.check_id,
    item.resolved_id,
    item.matched_node_id,
    item.node_id,
    item.matched_path_full,
    item.path_full,
    item.path,
    app,
    structure,
    field,
  ].forEach((value) => pushKey(keys, value));

  [
    [app, structure].filter(Boolean).join("."),
    [app, structure, field].filter(Boolean).join("."),
    [structure, field].filter(Boolean).join("."),
  ].forEach((value) => pushKey(keys, value));

  return [...keys];
}

function qualityIdentity(item: LineageQualityItem) {
  return text(item.check_id || item.id || item.resolved_id || item.matched_node_id || JSON.stringify(item));
}

function firstNonEmpty(...values: unknown[]) {
  return values.find((value) => value !== undefined && value !== null && text(value) !== "");
}

export function qualityText(value: unknown, fallback = "-") {
  const result = text(value);
  return result || fallback;
}

function statusValue(item: LineageQualityItem) {
  return firstNonEmpty(
    item.control_status,
    item.usage_quality_status,
    item.source_quality_status,
    item.field_quality_status,
    item.quality_status,
    item.status,
    item.confidence_level,
    item.__unresolved ? "UNRESOLVED" : ""
  );
}

function embeddedQualityItems(node: LineageNode): LineageQualityItem[] {
  const props = propsOf(node);
  const rawNode = node as unknown as Record<string, unknown>;
  const quality = (rawNode.quality || props.quality || {}) as Record<string, unknown>;
  const synthetic: LineageQualityItem[] = [];
  const usageScore = firstNonEmpty(quality.usage_quality_score, props.usage_quality_score);
  const statusScore = firstNonEmpty(quality.status_score, props.status_score);
  const usageStatus = firstNonEmpty(quality.usage_quality_status, props.usage_quality_status);
  const sourceScore = firstNonEmpty(quality.source_quality_score, props.source_quality_score);
  const sourceStatus = firstNonEmpty(quality.source_quality_status, props.source_quality_status);
  const fieldScore = firstNonEmpty(quality.field_quality_score, props.field_quality_score, quality.score, props.quality_score);
  const fieldStatus = firstNonEmpty(quality.field_quality_status, props.field_quality_status, quality.status, props.quality_status);

  if (usageScore !== undefined || statusScore !== undefined || usageStatus !== undefined) {
    synthetic.push({
      id: `${node.id}:usage-quality`,
      control_name: "Usage controls",
      usage_quality_score: usageScore,
      status_score: statusScore,
      usage_quality_status: usageStatus,
      control_status: usageStatus,
      score: usageScore,
      status: usageStatus,
    });
  }
  if (sourceScore !== undefined || sourceStatus !== undefined) {
    synthetic.push({
      id: `${node.id}:source-quality`,
      control_name: "Source quality",
      score: sourceScore,
      status: sourceStatus,
    });
  }
  if (fieldScore !== undefined || fieldStatus !== undefined) {
    synthetic.push({
      id: `${node.id}:field-quality`,
      control_name: "Field quality",
      score: fieldScore,
      status: fieldStatus,
    });
  }

  return [
    ...safeQualityItems(rawNode.quality_checks),
    ...safeQualityItems(props.quality_checks),
    ...safeQualityItems(props.controls),
    ...synthetic,
  ];
}

export function safeQualityItems(payload: unknown): LineageQualityItem[] {
  if (Array.isArray(payload)) return payload as LineageQualityItem[];
  if (payload && typeof payload === "object") {
    const source = payload as Record<string, unknown>;
    if (Array.isArray(source.items)) return source.items as LineageQualityItem[];
    if (Array.isArray(source.results)) return source.results as LineageQualityItem[];
    if (source.data && typeof source.data === "object" && Array.isArray((source.data as Record<string, unknown>).items)) {
      return (source.data as Record<string, unknown>).items as LineageQualityItem[];
    }
  }
  return [];
}

export function buildQualityIndex(items: LineageQualityItem[]): LineageQualityIndex {
  const index: LineageQualityIndex = new Map();
  items.forEach((item) => {
    qualityKeysForItem(item).forEach((key) => {
      const list = index.get(key) || [];
      list.push(item);
      index.set(key, list);
    });
  });
  return index;
}

export function mergeQualityItems(groups: Array<LineageQualityItem[] | undefined | null>): LineageQualityItem[] {
  const seen = new Set<string>();
  const merged: LineageQualityItem[] = [];
  groups.forEach((items) => {
    (items || []).forEach((item) => {
      const id = qualityIdentity(item);
      if (seen.has(id)) return;
      seen.add(id);
      merged.push(item);
    });
  });
  return merged;
}

export function collectQualityForNode(node: LineageNode, index: LineageQualityIndex): LineageQualityItem[] {
  const embedded = embeddedQualityItems(node);
  const indexed = qualityKeysForNode(node).flatMap((key) => index.get(key) || []);
  return mergeQualityItems([embedded, indexed]);
}

function qualityScore(item: LineageQualityItem) {
  const score = Number(item.score ?? item.quality_score ?? item.control_score);
  if (!Number.isFinite(score)) return null;
  return score <= 1 ? score * 100 : score;
}

export function qualityScoreLabel(item: LineageQualityItem) {
  const score = qualityScore(item);
  if (score === null) return "-";
  return `${Math.round(score * 10) / 10}%`;
}

export function usageQualityScoreLabel(item: LineageQualityItem) {
  const value = item.usage_quality_score ?? item.score ?? item.quality_score ?? item.control_score;
  const score = Number(value);
  if (!Number.isFinite(score)) return qualityText(value);
  return `${Math.round((score <= 1 ? score * 100 : score) * 10) / 10}%`;
}

export function statusScoreLabel(item: LineageQualityItem) {
  const value = item.status_score;
  const score = Number(value);
  if (!Number.isFinite(score)) return qualityText(value);
  return `${Math.round((score <= 1 ? score * 100 : score) * 10) / 10}%`;
}

export function qualityStatusLabel(item: LineageQualityItem) {
  return qualityText(statusValue(item), "-");
}

export function qualityControlName(item: LineageQualityItem) {
  return qualityText(
    item.control_name ||
      item.quality_dimension ||
      item.control_tool ||
      item.matched_path_full ||
      "Quality check",
    "Quality check"
  );
}

export function qualityControlTarget(item: LineageQualityItem) {
  return qualityText(
    item.matched_path_full ||
      [item.application_code_norm || item.application_code, item.controlled_structure_name, item.controlled_field_name]
        .filter(Boolean)
        .join(".") ||
      item.path_full ||
      item.path,
    "-"
  );
}

export function qualityCountLabel(item: LineageQualityItem) {
  const ok = qualityText(item.ok_count ?? item.okcount, "");
  const ko = qualityText(item.ko_count ?? item.kocount, "");
  const total = qualityText(item.controlled_item_count ?? item.controlleditemcount, "");
  if (!ok && !ko && !total) return "-";
  return `OK ${ok || "-"} / KO ${ko || "-"} / ${total || "-"}`;
}

export function qualityOutcomeForItems(items: LineageQualityItem[] = []): "ok" | "ko" | "unknown" {
  if (!items.length) return "unknown";
  const statuses = items.map((item) => normalizeQualityText(statusValue(item))).filter(Boolean);
  if (!statuses.length) return "unknown";
  if (statuses.some((status) => /(^|[^a-z])(ko|failed|failure|critical|error|unresolved)([^a-z]|$)/i.test(status))) return "ko";
  if (statuses.some((status) => /(^|[^a-z])(ok|passed|valid|validated|success|succeeded)([^a-z]|$)/i.test(status))) return "ok";
  return "unknown";
}

function aggregateStatusLabel(items: LineageQualityItem[]) {
  const statuses = items.map(qualityStatusLabel).filter((status) => status !== "-");
  const unique = [...new Set(statuses.map((status) => status.toUpperCase()))];
  if (unique.length === 1) return statuses[0];
  if (unique.length > 1) return "Mixed";
  return "Controls";
}

export function qualityBadgeForItems(items: LineageQualityItem[] = []): LineageQualityBadge | null {
  if (!items.length) return null;
  const outcome = qualityOutcomeForItems(items);
  if (outcome === "ko") return { tone: "critical", label: aggregateStatusLabel(items), count: items.length };
  if (outcome === "ok") return { tone: "good", label: aggregateStatusLabel(items), count: items.length };
  return { tone: "neutral", label: aggregateStatusLabel(items), count: items.length };
}

export function qualityTooltip(items: LineageQualityItem[] = []) {
  if (!items.length) return "";
  return items
    .slice(0, 5)
    .map((item) =>
      text(
        item.control_name ||
          item.quality_dimension ||
          item.control_tool ||
          item.control_status ||
          item.status ||
          item.matched_path_full ||
          "Quality check"
      )
    )
    .filter(Boolean)
    .join("\n");
}
