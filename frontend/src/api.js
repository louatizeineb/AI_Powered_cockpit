import axios from "axios";

const API_BASE_URL = "http://localhost:8000";

export async function fetchBusinessLineage(nodeId, depth = 2) {
  const response = await axios.get(
    `${API_BASE_URL}/lineage/business/${encodeURIComponent(nodeId)}`,
    {
      params: { depth },
    }
  );

  return response.data;
}

export async function searchAssets(query, limit = 10) {
  const response = await axios.get(`${API_BASE_URL}/search`, {
    params: {
      q: query,
      limit,
    },
  });

  return response.data;
}