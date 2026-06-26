from __future__ import annotations

from app.common.azure_openai import build_chat_llm_client, chat_llm_provider
from app.config import get_settings
from app.agent.prompts import DQC_AGENT_SYSTEM_PROMPT

settings = get_settings()


def explain_with_llm(task: str, evidence: dict) -> str:
    provider = chat_llm_provider(settings)
    if provider == "openai" and not all([settings.openai_api_key, settings.openai_chat_model]):
        return "OpenAI LLM is not configured. Deterministic explanation is available in the workflow response."
    if provider == "azure_openai" and not all([settings.azure_openai_endpoint, settings.azure_openai_api_key, settings.azure_openai_chat_deployment]):
        return "Azure OpenAI LLM is not configured. Deterministic explanation is available in the workflow response."
    client, model_name, _provider, timeout_seconds = build_chat_llm_client(settings)
    if hasattr(client, "with_options"):
        client = client.with_options(timeout=timeout_seconds, max_retries=0)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": DQC_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}\nEvidence: {evidence}"},
        ],
        max_completion_tokens=max(64, int(settings.llm_max_completion_tokens or 700)),
    )
    return response.choices[0].message.content or ""
