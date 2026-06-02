import axios from "axios";

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const api = axios.create({
  baseURL: API_BASE_URL,
  // Long uploads/processes can exceed 2 minutes. 0 = no client timeout.
  timeout: 0,
});

function normalizeApiError(error) {
  if (error?.response?.data) {
    const data = error.response.data;
    if (typeof data === "string") return data;
    if (data.detail) return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail, null, 2);
    return JSON.stringify(data, null, 2);
  }
  if (error?.code === "ERR_NETWORK") {
    return "Network Error: backend unreachable or CORS blocked the request. Check FastAPI CORS and that uvicorn is running.";
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

export async function fetchBusinessLineage(nodeId, depth = 2) {
  return request(() =>
    api.get(`/lineage/business/${encodeURIComponent(nodeId)}`, {
      params: { depth },
    })
  );
}

export async function searchAssets(query, limit = 10) {
  return request(() => api.get(`/search`, { params: { q: query, limit } }));
}

export async function connectDqcDatabase({ tableName = "DQC", limit = 1000 }) {
  return request(() =>
    api.post(`/dqc-resolution/connect/database`, {
      table_name: tableName,
      limit: Number(limit),
    })
  );
}

export async function uploadDqcFile(file) {
  const formData = new FormData();
  formData.append("file", file, file.name);

  // Do NOT manually set multipart Content-Type. The browser must add the boundary.
  return request(() => api.post(`/dqc-resolution/upload`, formData));
}

export async function resetDqcWorkspace() {
  return request(() => api.post(`/dqc-resolution/reset-workspace`));
}

export async function fetchResolvedDqc(limit = 100) {
  return request(() => api.get(`/dqc-resolution/resolved`, { params: { limit } }));
}

export async function fetchUnresolvedDqc(limit = 100) {
  return request(() => api.get(`/dqc-resolution/unresolved`, { params: { limit } }));
}

export async function approveDqcMatch(resolvedId, payload) {
  return request(() =>
    api.post(`/dqc-resolution/review/${encodeURIComponent(resolvedId)}/approve`, payload)
  );
}

export async function rejectDqcMatch(resolvedId, payload) {
  return request(() =>
    api.post(`/dqc-resolution/review/${encodeURIComponent(resolvedId)}/reject`, payload)
  );
}

export async function askDqcAgent(message) {
  return request(() => api.post(`/agent/dqc/chat`, { message }));
}

export async function runDqcAgentWorkflow(event, useLlmExplanation = true) {
  return request(() =>
    api.post(`/agent/dqc/run-workflow`, {
      event,
      use_llm_explanation: useLlmExplanation,
    })
  );
}

export async function fetchPipelineLogs(limit = 100) {
  return request(() => api.get(`/observability/logs`, { params: { limit } }));
}

export async function fetchEntityQualityProfile(nodeId) {
  return request(() =>
    api.get(`/dqc-resolution/entity/${encodeURIComponent(nodeId)}/quality-profile`)
  );
}
