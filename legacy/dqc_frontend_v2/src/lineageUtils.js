export function graphToCytoscapeElements(graph) {
  const nodes = graph.nodes.map((node) => ({
    data: {
      id: node.id,
      label: node.label || node.id,
      type: node.type || "Node",
      node_id: node.node_id,
      properties: node.properties || {},
      collapsed: false,
      hiddenByCollapse: false,
    },
  }));

  const edges = graph.edges.map((edge) => ({
    data: {
      id: edge.id,
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
  const normalized = String(type || "").toLowerCase();

  if (normalized.includes("source")) return "#1f6feb";
  if (normalized.includes("container")) return "#18a8c7";
  if (normalized.includes("structure")) return "#21a67a";
  if (normalized.includes("field")) return "#f59e0b";
  if (normalized.includes("usage")) return "#7c3aed";
  if (normalized.includes("business")) return "#e05252";
  if (normalized.includes("dataprocessing")) return "#fb7185";

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
        "overlay-padding": 6,
      },
    },
    {
      selector: `node[id = "${rootId}"]`,
      style: {
        width: 56,
        height: 56,
        "border-width": 5,
        "border-color": "#10233f",
        "font-size": 11,
        "font-weight": "bold",
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
        "text-background-color": "#ffffff",
        "text-background-opacity": 0.85,
        "text-background-padding": 2,
      },
    },
    {
      selector: ".collapsed",
      style: {
        shape: "rectangle",
        "background-color": "#10233f",
        color: "#10233f",
      },
    },
    {
      selector: ".hidden",
      style: {
        display: "none",
      },
    },
    {
      selector: ":selected",
      style: {
        "border-width": 5,
        "border-color": "#f59e0b",
      },
    },
  ];
}