from __future__ import annotations

from functools import lru_cache

from openai import APIConnectionError, APIStatusError

from app.common.azure_openai import build_azure_openai_client
from app.config import get_settings
from app.embeddings.vector import local_hash_embedding

settings = get_settings()


@lru_cache(maxsize=1)
def _azure_client():
    return build_azure_openai_client(settings)


def embed_text(text: str) -> list[float]:
    provider = settings.embedding_provider.strip().lower()

    if provider == "azure_openai":
        if not all([settings.azure_openai_endpoint, settings.azure_openai_api_key, settings.azure_openai_embedding_deployment]):
            raise RuntimeError("Azure OpenAI embedding config is incomplete")
        client = _azure_client()
        try:
            response = client.embeddings.create(
                model=settings.azure_openai_embedding_deployment,
                input=text,
            )
        except APIStatusError as exc:
            raise RuntimeError(
                "Azure OpenAI embedding request failed. "
                f"Endpoint={settings.azure_openai_endpoint!r}, "
                f"deployment/model={settings.azure_openai_embedding_deployment!r}, "
                f"HTTP {exc.status_code}, response={exc.response.text}"
            ) from exc
        except APIConnectionError as exc:
            raise RuntimeError(
                "Azure OpenAI embedding connection failed. "
                f"Endpoint={settings.azure_openai_endpoint!r}, "
                f"deployment/model={settings.azure_openai_embedding_deployment!r}, "
                f"error={exc}"
            ) from exc
        return response.data[0].embedding

    if provider == "local_hash":
        return local_hash_embedding(text, dim=settings.embedding_dim)

    raise RuntimeError(f"Unsupported EMBEDDING_PROVIDER={settings.embedding_provider!r}. Use 'azure_openai' or 'local_hash'.")
