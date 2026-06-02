import axios from "axios";
import type {
  LineageDirection,
  LineageNeighborsResponse,
  LineageSearchResponse,
} from "../types/lineage.types";

export const LINEAGE_API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8001";

const api = axios.create({
  baseURL: LINEAGE_API_BASE_URL,
});

function normalizeApiError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data;
    if (typeof data === "string") return data;
    if (data?.detail) return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    if (data) return JSON.stringify(data);
    if (error.code === "ERR_NETWORK") {
      return "Network Error: backend unreachable or CORS blocked the request.";
    }
    return error.message;
  }
  return error instanceof Error ? error.message : "Unknown lineage API error";
}

async function request<T>(fn: () => Promise<{ data: T }>): Promise<T> {
  try {
    const response = await fn();
    return response.data;
  } catch (error) {
    throw new Error(normalizeApiError(error));
  }
}

export function searchLineageEntities(q: string, limit = 20, signal?: AbortSignal) {
  return request<LineageSearchResponse>(() =>
    api.get("/lineage/explorer/search", {
      params: { q, limit },
      signal,
    })
  );
}

export function fetchLineageNeighbors(
  nodeId: string,
  direction: LineageDirection,
  limit = 50
) {
  return request<LineageNeighborsResponse>(() =>
    api.get(`/lineage/explorer/node/${encodeURIComponent(nodeId)}/neighbors`, {
      params: { direction, limit },
    })
  );
}

export function fetchLineageSourceContext(
  nodeId: string,
  catalogOffset = 0,
  catalogLimit = 500,
  consumerLimit = 300
) {
  return request<LineageNeighborsResponse>(() =>
    api.get(`/lineage/explorer/node/${encodeURIComponent(nodeId)}/source-context`, {
      params: { catalog_offset: catalogOffset, catalog_limit: catalogLimit, consumer_limit: consumerLimit },
    })
  );
}
