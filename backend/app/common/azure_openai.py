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
        api_version=settings.azure_openai_api_version or "2024-10-21",
    )


def chat_llm_provider(settings: Settings) -> str:
    provider = (settings.llm_provider or "").strip().lower()
    if provider in {"openai", "azure_openai", "azure"}:
        return "azure_openai" if provider == "azure" else provider
    if settings.openai_api_key:
        return "openai"
    if settings.llm_disable_azure_fallback:
        return "openai"
    return "azure_openai"


def build_openai_client(settings: Settings) -> OpenAI:
    kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url.rstrip("/") + "/"
    return OpenAI(**kwargs)


def build_chat_llm_client(settings: Settings) -> tuple[Any, str, str, float]:
    provider = chat_llm_provider(settings)
    if provider == "openai":
        return (
            build_openai_client(settings),
            settings.openai_chat_model or "gpt-5.4-mini",
            "openai",
            settings.openai_timeout_seconds or settings.azure_openai_timeout_seconds,
        )
    return (
        build_azure_openai_client(settings),
        settings.azure_openai_chat_deployment,
        "azure_openai",
        settings.azure_openai_timeout_seconds,
    )
