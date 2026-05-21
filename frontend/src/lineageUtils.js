export function normalizeType(type) {
  return String(type || "node").toLowerCase();
}

export function getEntityIcon(type) {
  const normalized = normalizeType(type);
  if (normalized.includes("source")) return "DB";
  if (normalized.includes("container")) return "▤";
  if (normalized.includes("structure")) return "▥";
  if (normalized.includes("field") || normalized.includes("column")) return "◉";
  if (normalized.includes("usage")) return "U";
  if (normalized.includes("business")) return "BT";
  if (normalized.includes("dataprocessing") || normalized.includes("process")) return "↔";
  return "▣";
}

export function getNodeColor(type) {
  const normalized = normalizeType(type);
  if (normalized.includes("source")) return "#3b82f6";
  if (normalized.includes("container")) return "#14b8a6";
  if (normalized.includes("structure")) return "#10b981";
  if (normalized.includes("field") || normalized.includes("column")) return "#22c55e";
  if (normalized.includes("usage")) return "#8b5cf6";
  if (normalized.includes("business")) return "#f59e0b";
  if (normalized.includes("dataprocessing") || normalized.includes("process")) return "#4f46e5";
  return "#64748b";
}

export function getNodeAccent(type) {
  const normalized = normalizeType(type);
  if (normalized.includes("source")) return "#e8f1ff";
  if (normalized.includes("container")) return "#e8fffb";
  if (normalized.includes("structure")) return "#ecfdf5";
  if (normalized.includes("field") || normalized.includes("column")) return "#f0fdf4";
  if (normalized.includes("usage")) return "#f5f3ff";
  if (normalized.includes("business")) return "#fff7ed";
  if (normalized.includes("dataprocessing") || normalized.includes("process")) return "#eef2ff";
  return "#f8fafc";
}

export function getQualityStatus(node) {
  const p = node?.properties || node?.data?.properties || {};
  const score = Number(p.quality_score ?? p.score ?? p.dqc_score ?? p.qualityScore);
  const koRate = Number(p.ko_rate ?? p.koRate ?? p.error_rate);
  const severity = String(p.severity || p.quality_severity || "").toLowerCase();
  const status = String(p.quality_status || p.status || "").toLowerCase();

  if (severity.includes("critical") || severity.includes("high") || status.includes("ko") || koRate > 0.2 || score < 70) {
    return { level: "bad", symbol: "!", hint: "Critical or high-quality issue detected." };
  }
  if (severity.includes("warning") || status.includes("warning") || koRate > 0 || (score >= 70 && score < 90)) {
    return { level: "warning", symbol: "⚠", hint: "Needs attention or review." };
  }
  if (Number.isFinite(score) || Number.isFinite(koRate) || status.includes("ok") || status.includes("valid")) {
    return { level: "good", symbol: "✓", hint: "Validated or no KO rate detected." };
  }
  return { level: "unknown", symbol: "•", hint: "No quality metric linked yet." };
}

function getLabel(node) {
  const label = node.label || node.name || node.name_label || node.name_tech || node.id || "Asset";
  return String(label).length > 28 ? `${String(label).slice(0, 27)}…` : String(label);
}

function getSubtitle(node) {
  const p = node.properties || {};
  const raw = p.path_full || p.path || p.full_path || p.qualified_name || p.name_tech || node.node_id || "";
  const clean = String(raw).replace(/\\/g, " › ").replace(/^\s*›\s*/, "");
  return clean.length > 36 ? `${clean.slice(0, 35)}…` : clean;
}

function getCardLabel(node) {
  const type = String(node.type || "Asset");
  const quality = getQualityStatus(node);
  return `${getEntityIcon(type)}  ${getLabel(node)}\n${type}  ·  ${quality.symbol} ${quality.level}`;
}

export function graphToDatagalaxyElements(graph, options = {}) {
  const onlyIssues = Boolean(options.onlyIssues);
  const rawNodes = graph?.nodes || [];
  const included = new Set();

  const nodes = rawNodes
    .filter((node) => {
      if (!onlyIssues) return true;
      const level = getQualityStatus(node).level;
      return level === "warning" || level === "bad";
    })
    .map((node) => {
      const status = getQualityStatus(node);
      included.add(node.id);
      return {
        data: {
          id: node.id,
          label: getCardLabel(node),
          title: getLabel(node),
          subtitle: getSubtitle(node),
          type: node.type || "Node",
          node_id: node.node_id,
          properties: node.properties || {},
          qualityLevel: status.level,
          qualitySymbol: status.symbol,
          bg: getNodeAccent(node.type),
          color: getNodeColor(node.type),
          collapsed: false,
          hiddenByCollapse: false,
        },
        classes: `${status.level} ${normalizeType(node.type)}`,
      };
    });

  const edges = (graph?.edges || [])
    .filter((edge) => included.has(edge.source) && included.has(edge.target))
    .map((edge, index) => ({
      data: {
        id: edge.id || `${edge.source}-${edge.target}-${index}`,
        source: edge.source,
        target: edge.target,
        label: edge.type || edge.label || "RELATES_TO",
        type: edge.type || edge.label || "RELATES_TO",
        properties: edge.properties || {},
      },
    }));

  return [...nodes, ...edges];
}

export function graphToCytoscapeElements(graph) {
  return graphToDatagalaxyElements(graph);
}

export function createDatagalaxyStylesheet(rootId) {
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "text-wrap": "wrap",
        "text-max-width": 160,
        "text-valign": "center",
        "text-halign": "center",
        "font-size": 11,
        "font-family": "Inter, Segoe UI, Arial, sans-serif",
        "font-weight": 700,
        color: "#172033",
        shape: "round-rectangle",
        width: 218,
        height: 78,
        "background-color": "data(bg)",
        "border-width": 2,
        "border-color": "data(color)",
        "border-opacity": 0.85,
        "overlay-padding": 10,
        "overlay-opacity": 0,
        "shadow-blur": 18,
        "shadow-color": "#0f172a",
        "shadow-opacity": 0.10,
        "shadow-offset-x": 0,
        "shadow-offset-y": 8,
      },
    },
    {
      selector: `node[id = "${rootId}"]`,
      style: {
        width: 238,
        height: 88,
        "font-size": 12,
        "border-width": 3,
        "border-color": "#7c3aed",
        "background-color": "#f4edff",
        "shadow-opacity": 0.18,
      },
    },
    {
      selector: "node.warning",
      style: {
        "border-color": "#f59e0b",
        "background-color": "#fffbeb",
      },
    },
    {
      selector: "node.bad",
      style: {
        "border-color": "#ec4899",
        "background-color": "#fff1f5",
      },
    },
    {
      selector: "node.good",
      style: {
        "border-color": "#22c55e",
      },
    },
    {
      selector: "edge",
      style: {
        label: "data(label)",
        width: 1.65,
        "line-color": "#7da0ff",
        "target-arrow-color": "#7da0ff",
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        "control-point-step-size": 52,
        "font-size": 8,
        "font-weight": 650,
        color: "#64748b",
        "text-background-color": "#ffffff",
        "text-background-opacity": 0.86,
        "text-background-padding": 3,
        "arrow-scale": 0.8,
      },
    },
    {
      selector: "edge[type *= 'Output'], edge[type *= 'OUTPUT']",
      style: {
        "line-color": "#ec4899",
        "target-arrow-color": "#ec4899",
        width: 2.2,
      },
    },
    {
      selector: "edge[type *= 'Input'], edge[type *= 'INPUT']",
      style: {
        "line-color": "#3b82f6",
        "target-arrow-color": "#3b82f6",
      },
    },
    {
      selector: ".collapsed",
      style: {
        "border-style": "dashed",
        "background-color": "#f8fafc",
        color: "#64748b",
      },
    },
    {
      selector: ".hidden",
      style: { display: "none" },
    },
    {
      selector: ":selected",
      style: {
        "border-width": 4,
        "border-color": "#0ea5e9",
        "shadow-opacity": 0.25,
      },
    },
  ];
}

export function createStylesheet(rootId) {
  return createDatagalaxyStylesheet(rootId);
}
