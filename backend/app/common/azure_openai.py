from __future__ import annotations

from typing import Any

from openai import AzureOpenAI, OpenAI

from app.config import Settings


def normalize_openai_v1_endpoint(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    openai_v1_marker = "/openai/v1"
    if openai_v1_marker in endpoint:
        return endpoint.split(openai_v1_marker, 1)[0] + openai_v1_marker
    return f"{endpoint}/openai/v1"


def build_azure_openai_client(settings: Settings) -> Any:
    endpoint = settings.azure_openai_endpoint.rstrip("/")
    if "services.ai.azure.com" in endpoint:
        endpoint = normalize_openai_v1_endpoint(endpoint)
        return OpenAI(
            api_key=settings.azure_openai_api_key,
            base_url=f"{endpoint}/",
        )

    return AzureOpenAI(
        api_key=settings.azure_openai_api_key,
        azure_endpoint=endpoint,
        api_version=settings.azure_openai_api_version,
    )
