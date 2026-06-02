export type LineageDirection = "upstream" | "downstream";

export type LineageCategory =
  | "field"
  | "structure"
  | "dataset"
  | "usage"
  | "processing"
  | "processing_item"
  | "source"
  | "control"
  | "asset";

export type LineageVisualRole =
  | "golden_source"
  | "source_asset"
  | "data_processing"
  | "data_processing_item"
  | "intermediate_asset"
  | "final_asset"
  | "usage";

export type LineageGroupType = "source_table" | "data_processing" | "dataset" | "usage";

export type LineageNode = {
  id: string;
  node_id: string;
  label: string;
  technical_name?: string | null;
  type: string;
  category: LineageCategory | string;
  entity_type?: string | null;
  data_type?: string | null;
  path_full?: string | null;
  path_type?: string | null;
  parent_node_id?: string | null;
  parent_label?: string | null;
  parent_type?: string | null;
  path?: string | null;
  visual_role?: LineageVisualRole | string | null;
  group_id?: string | null;
  group_type?: LineageGroupType | string | null;
  group_label?: string | null;
  has_upstream: boolean;
  has_downstream: boolean;
  depth: number;
  properties?: Record<string, unknown>;
};

export type LineageEdge = {
  id: string;
  source: string;
  target: string;
  raw_source?: string | null;
  raw_target?: string | null;
  type: string;
  raw_type?: string | null;
  direction: LineageDirection;
  visual_source?: string;
  visual_target?: string;
  is_visual_reversed?: boolean;
  properties?: Record<string, unknown>;
};

export type LineageSearchResult = Omit<LineageNode, "depth">;

export type LineageNeighborsResponse = {
  center: LineageSearchResult;
  nodes: LineageSearchResult[];
  edges: LineageEdge[];
};

export type LineageSearchResponse = {
  query: string;
  count: number;
  results: LineageSearchResult[];
};

export type LineagePosition = {
  x: number;
  y: number;
};

export type HighlightDirection = LineageDirection | "branch";

export type HighlightedPath = {
  id: string;
  sourceNodeId: string;
  direction: HighlightDirection;
  color: string;
  nodeIds: string[];
  edgeIds: string[];
};

export type LineageGraphState = {
  nodes: LineageNode[];
  edges: LineageEdge[];
  expanded: {
    upstream: Record<string, boolean>;
    downstream: Record<string, boolean>;
  };
  focusedNodeId: string | null;
  highlights: HighlightedPath[];
};
