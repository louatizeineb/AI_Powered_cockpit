from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from openai import APIConnectionError
    from openai import APIStatusError

    from app.common.azure_openai import build_azure_openai_client
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing dependency: openai. Install it with "
        "`python -m pip install -r backend/requirements.txt`."
    ) from exc

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    missing = [
        name
        for name, value in {
            "AZURE_OPENAI_ENDPOINT": settings.azure_openai_endpoint,
            "AZURE_OPENAI_API_KEY": settings.azure_openai_api_key,
            "AZURE_OPENAI_CHAT_DEPLOYMENT": settings.azure_openai_chat_deployment,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit(
            "Missing Azure OpenAI settings in backend/.env or "
            f"backend/.env.agent_and_resolution.additions: {', '.join(missing)}"
        )

    client = build_azure_openai_client(settings)
    try:
        model_ids = [model.id for model in client.models.list()]
    except APIStatusError as exc:
        raise SystemExit(
            f"Azure OpenAI models-list failed: HTTP {exc.status_code}. "
            f"Response: {exc.response.text}"
        ) from exc
    except APIConnectionError as exc:
        raise SystemExit(f"Azure OpenAI connection failed while listing models: {exc}") from exc

    if settings.azure_openai_chat_deployment not in model_ids:
        similar = [
            model_id
            for model_id in model_ids
            if settings.azure_openai_chat_deployment.lower() in model_id.lower()
            or "gpt" in model_id.lower()
        ][:20]
        raise SystemExit(
            f"Configured chat deployment/model '{settings.azure_openai_chat_deployment}' "
            f"was not found by this endpoint. Similar models: {similar}"
        )

    print(f"Endpoint auth OK. Found chat model/deployment: {settings.azure_openai_chat_deployment}")

    try:
        response = client.chat.completions.create(
            model=settings.azure_openai_chat_deployment,
            messages=[{"role": "user", "content": "Say: Azure connection works."}],
        )
    except APIStatusError as exc:
        raise SystemExit(
            f"Azure OpenAI request failed: HTTP {exc.status_code}. "
            f"Endpoint auth and model lookup succeeded, so check that "
            f"'{settings.azure_openai_chat_deployment}' is an active chat-capable "
            f"deployment in Azure AI Foundry. Response: {exc.response.text}"
        ) from exc
    except APIConnectionError as exc:
        raise SystemExit(f"Azure OpenAI connection failed: {exc}") from exc

    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
