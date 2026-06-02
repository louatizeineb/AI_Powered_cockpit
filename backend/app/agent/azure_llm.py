from __future__ import annotations

from app.common.azure_openai import build_azure_openai_client
from app.config import get_settings
from app.agent.prompts import DQC_AGENT_SYSTEM_PROMPT

settings = get_settings()


def explain_with_llm(task: str, evidence: dict) -> str:
    if not all([settings.azure_openai_endpoint, settings.azure_openai_api_key, settings.azure_openai_chat_deployment]):
        return "Azure LLM is not configured. Deterministic explanation is available in the workflow response."
    client = build_azure_openai_client(settings)
    response = client.chat.completions.create(
        model=settings.azure_openai_chat_deployment,
        messages=[
            {"role": "system", "content": DQC_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}\nEvidence: {evidence}"},
        ],
        temperature=0.1,
    )
    return response.choices[0].message.content or ""
