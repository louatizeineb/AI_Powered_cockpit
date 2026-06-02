import axios from "axios";

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8001";

export const DQC_API_BASE_URLS = [
  ...(import.meta.env.VITE_DQC_API_BASE_URLS
    ? String(import.meta.env.VITE_DQC_API_BASE_URLS).split(",")
    : [API_BASE_URL]),
]
  .map((value) => String(value || "").trim().replace(/\/+$/, ""))
  .filter(Boolean)
  .filter((value, index, list) => list.indexOf(value) === index);

const api = axios.create({
  baseURL: API_BASE_URL,
});

const dqcApis = DQC_API_BASE_URLS.map((baseURL) =>
  axios.create({
    baseURL,
  })
);

function normalizeApiError(error) {
  if (error?.response?.data) {
    const data = error.response.data;
    if (typeof data === "string") return data;
    if (data.detail) return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail, null, 2);
    return JSON.stringify(data, null, 2);
  }
  if (error?.code === "ERR_NETWORK") {
    return "Network Error: backend unreachable or CORS blocked the request.";
  }
  return error?.message || "Unknown API error";
}

async function request(fn) {
  try {
    const response = await fn();
    return response.data;
  } catch (error) {
    throw new Error(normalizeApiError(error));
  }
}

async function requestAllDqc(path, { params } = {}) {
  const settled = await Promise.allSettled(
    dqcApis.map(async (client) => {
      const response = await client.get(path, { params });
      return {
        baseURL: client.defaults.baseURL,
        data: response.data,
      };
    })
  );

  const fulfilled = settled
    .filter((result) => result.status === "fulfilled")
    .map((result) => result.value);
  if (fulfilled.length) return fulfilled;

  const errors = settled
    .filter((result) => result.status === "rejected")
    .map((result, index) => `${dqcApis[index]?.defaults?.baseURL || "backend"}: ${normalizeApiError(result.reason)}`);
  throw new Error(
    errors.length
      ? `No DQC backend answered ${path}. Tried: ${errors.join(" | ")}`
      : "No DQC backend configured. Set VITE_DQC_API_BASE_URLS."
  );
}

async function requestFirstDqc(makeRequest) {
  const errors = [];
  for (const client of dqcApis) {
    try {
      const response = await makeRequest(client);
      return {
        ...response.data,
        __dqc_backend: client.defaults.baseURL,
      };
    } catch (error) {
      errors.push(`${client.defaults.baseURL}: ${normalizeApiError(error)}`);
    }
  }
  throw new Error(
    errors.length
      ? `No DQC backend accepted the request. Start backend or set VITE_DQC_API_BASE_URLS. Tried: ${errors.join(" | ")}`
      : "No DQC backend configured. Set VITE_DQC_API_BASE_URLS to the backend exposing /dqc-resolution/upload."
  );
}

function itemsFromPayload(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.results)) return payload.results;
  if (Array.isArray(payload?.data?.items)) return payload.data.items;
  return [];
}

function qualityItemKey(item) {
  return String(
    item?.check_id ||
      item?.id ||
      item?.resolved_id ||
      item?.matched_node_id ||
      item?.matched_path_full ||
      JSON.stringify(item)
  );
}

function mergeDqcPayloads(payloads, marker) {
  const seen = new Set();
  const items = [];
  payloads.forEach(({ baseURL, data }) => {
    itemsFromPayload(data).forEach((item) => {
      const enriched = {
        ...item,
        __dqc_backend: baseURL,
        ...(marker || {}),
      };
      const key = `${marker?.__unresolved ? "unresolved" : "resolved"}:${qualityItemKey(enriched)}`;
      if (seen.has(key)) return;
      seen.add(key);
      items.push(enriched);
    });
  });
  return { items, sources: payloads.map((payload) => payload.baseURL) };
}

export async function connectDqcDatabase({ tableName = "DQC", limit = 1000 }) {
  return requestFirstDqc((client) =>
    client.post("/dqc-resolution/connect/database", {
      table_name: tableName,
      limit: Number(limit),
    })
  );
}

export async function uploadDqcFile(file) {
  return requestFirstDqc((client) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    return client.post("/dqc-resolution/upload", formData);
  });
}

export async function resetDqcWorkspace() {
  return requestFirstDqc((client) => client.post("/dqc-resolution/reset-workspace"));
}

export async function fetchResolvedDqc(limit = 100) {
  const payloads = await requestAllDqc("/dqc-resolution/resolved", { params: { limit } });
  return mergeDqcPayloads(payloads);
}

export async function fetchUnresolvedDqc(limit = 100) {
  const payloads = await requestAllDqc("/dqc-resolution/unresolved", { params: { limit } });
  return mergeDqcPayloads(payloads, { __unresolved: true });
}

export async function approveDqcMatch(resolvedId, payload) {
  return requestFirstDqc((client) =>
    client.post(`/dqc-resolution/review/${encodeURIComponent(resolvedId)}/approve`, payload)
  );
}

export async function rejectDqcMatch(resolvedId, payload) {
  return requestFirstDqc((client) =>
    client.post(`/dqc-resolution/review/${encodeURIComponent(resolvedId)}/reject`, payload)
  );
}

export async function askDqcAgent(message) {
  return requestFirstDqc((client) => client.post("/agent/dqc/chat", { message }));
}

export async function runDqcAgentWorkflow(event, useLlmExplanation = true) {
  return requestFirstDqc((client) =>
    client.post("/agent/dqc/run-workflow", {
      event,
      use_llm_explanation: useLlmExplanation,
    })
  );
}

export async function fetchPipelineLogs(limit = 100) {
  const payloads = await requestAllDqc("/observability/logs", { params: { limit } });
  return mergeDqcPayloads(payloads, { __log: true });
}

export async function fetchLegacyQualityResults(limit = 100) {
  const payloads = await requestAllDqc("/events/quality-results", { params: { limit } });
  return mergeDqcPayloads(payloads, { __legacy_quality_result: true });
}

export async function fetchEntityQualityProfile(nodeId) {
  const payloads = await requestAllDqc(`/dqc-resolution/entity/${encodeURIComponent(nodeId)}/quality-profile`);
  return mergeDqcPayloads(payloads, { __quality_profile: true });
}
