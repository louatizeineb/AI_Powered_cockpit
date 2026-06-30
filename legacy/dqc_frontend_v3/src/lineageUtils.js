export function normalizeText(value) {
  return String(value || "").trim().toLowerCase();
}

export function getNodeName(node) {
  const props = node?.properties || {};
  return (
    node?.label ||
    props.name_label ||
    props.name ||
    props.name_tech ||
    props.technical_name ||
    props.source_name ||
    props.structure_name ||
    props.field_name ||
    props.data_processing_name ||
    props.usage_name ||
    node?.node_id ||
    node?.id ||
    "Unnamed asset"
  );
}

export function getNodePath(node) {
  const props = node?.properties || {};
  return (
    props.path_full ||
    props.path ||
    props.technical_path ||
    props.source_path ||
    props.structure_path ||
    props.usage_path ||
    props.container_name ||
    props.application_code ||
    props.app_code ||
    node?.node_id ||
    node?.id ||
    ""
  );
}

export function classifyAssetType(type) {
  const value = normalizeText(type);
  if (
    value.includes("source") ||
    value.includes("filestore") ||
    value === "db" ||
    value.includes("database")
  ) {
    return "source";
  }
  if (
    value.includes("process") ||
    value.includes("dataprocessing") ||
    value.includes("pipeline") ||
    value.includes("job") ||
    value.includes("traitement")
  ) {
    return "process";
  }
  if (value.includes("structure") || value.includes("table")) return "structure";
  if (value.includes("field") || value.includes("column")) return "field";
  if (
    value.includes("usage") ||
    value.includes("dashboard") ||
    value.includes("report") ||
    value.includes("api") ||
    value.includes("application") ||
    value === "app"
  ) {
    return "usage";
  }
  if (value.includes("dataset") || value.includes("data set")) return "dataset";
  return "dataset";
}

export function assetIcon(type) {
  const family = classifyAssetType(type);
  if (family === "source") return "SRC";
  if (family === "process") return "PRC";
  if (family === "structure") return "TBL";
  if (family === "field") return "FLD";
  if (family === "usage") {
    const value = normalizeText(type);
    if (value.includes("dashboard")) return "DSH";
    if (value.includes("api")) return "API";
    if (value.includes("app")) return "APP";
    return "USE";
  }
  return "DTS";
}

export function graphToCytoscapeElements(graph) {
  const nodes = (graph?.nodes || []).map((node) => ({
    data: {
      id: node.id,
      label: getNodeName(node),
      type: node.type || "Node",
      node_id: node.node_id,
      properties: node.properties || {},
    },
  }));

  const edges = (graph?.edges || []).map((edge, index) => ({
    data: {
      id: edge.id || `${edge.source}-${edge.target}-${index}`,
      source: edge.source,
      target: edge.target,
      label: edge.type,
      type: edge.type,
      properties: edge.properties || {},
    },
  }));

  return [...nodes, ...edges];
}

export function getNodeColor(type) {
  const family = classifyAssetType(type);
  if (family === "source") return "#1f6feb";
  if (family === "process") return "#7c3aed";
  if (family === "structure") return "#0284c7";
  if (family === "field") return "#f59e0b";
  if (family === "usage") return "#13a066";
  return "#667085";
}

export function createStylesheet(rootId) {
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "background-color": (ele) => getNodeColor(ele.data("type")),
        color: "#10233f",
        "font-size": 9,
        "font-weight": 650,
        "text-valign": "bottom",
        "text-halign": "center",
        "text-wrap": "wrap",
        "text-max-width": 130,
        width: 38,
        height: 38,
        "border-width": 3,
        "border-color": "#ffffff",
      },
    },
    {
      selector: `node[id = "${rootId}"]`,
      style: {
        width: 56,
        height: 56,
        "border-width": 5,
        "border-color": "#10233f",
      },
    },
    {
      selector: "edge",
      style: {
        label: "data(label)",
        width: 1.6,
        "line-color": "#9fb3c8",
        "target-arrow-color": "#9fb3c8",
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        "font-size": 7,
        color: "#667085",
      },
    },
  ];
}
