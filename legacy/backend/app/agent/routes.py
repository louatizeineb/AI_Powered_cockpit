from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.agent.workflow import run_fixed_workflow
from app.agent.azure_llm import explain_with_llm
from app.agent.tools import tool_list_unresolved, tool_list_resolved

router = APIRouter(prefix="/agent/dqc", tags=["DQC Agent"])


class AgentWorkflowRequest(BaseModel):
    event: dict
    use_llm_explanation: bool = False


class AgentChatRequest(BaseModel):
    message: str
    limit: int = 10


@router.post("/run-workflow")
def run_workflow(payload: AgentWorkflowRequest):
    result = run_fixed_workflow(payload.event)
    if payload.use_llm_explanation:
        result["llm_explanation"] = explain_with_llm("Explain DQC matching workflow result", result)
    return result


@router.post("/chat")
def simple_agent_chat(payload: AgentChatRequest):
    """Simple controlled agent endpoint for demo; it uses tools based on intent keywords."""
    msg = payload.message.lower()
    if "unresolved" in msg or "dlq" in msg:
        data = tool_list_unresolved(payload.limit)
        explanation = explain_with_llm(payload.message, data)
        return {"tool_used": "list_unresolved", "data": data, "explanation": explanation}
    if "resolved" in msg or "matched" in msg:
        data = tool_list_resolved(payload.limit)
        explanation = explain_with_llm(payload.message, data)
        return {"tool_used": "list_resolved", "data": data, "explanation": explanation}
    return {"message": "Ask about unresolved/DLQ events, resolved matches, or use /agent/dqc/run-workflow with a DQC event."}
