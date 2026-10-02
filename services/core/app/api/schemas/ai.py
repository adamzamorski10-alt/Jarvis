"""AI API schemas."""
from datetime import datetime
from pydantic import BaseModel, Field


class AIProviderItem(BaseModel):
    id: str
    label: str
    available: bool


class AIConfigResponse(BaseModel):
    provider: str
    ollama_url: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key_configured: bool = False
    openrouter_api_key_hint: str | None = None
    kimi_base_url: str
    kimi_api_key_configured: bool = False
    kimi_api_key_hint: str | None = None
    selected_model: str | None


class UpdateAIConfigRequest(BaseModel):
    provider: str = "ollama"
    ollama_url: str | None = None
    openrouter_base_url: str | None = None
    openrouter_api_key: str | None = None
    kimi_base_url: str | None = None
    kimi_api_key: str | None = None
    selected_model: str | None = None


class AIStatusResponse(BaseModel):
    provider: str
    provider_status: str
    configured: bool
    provider_url: str | None
    provider_version: str | None = None
    message: str | None = None
    model: str | None
    model_status: str
    models_count: int
    ai_ready: bool
    installed: bool | None = None
    service_running: bool | None = None
    api_responding: bool | None = None
    models_available: bool | None = None
    executable_path: str | None = None


class AIModelResponse(BaseModel):
    name: str
    size: int | None = None
    modified_at: str | None = None


class MessageResponse(BaseModel):
    id: int
    conversation_id: str
    role: str
    content: str
    created_at: datetime


class ConversationResponse(BaseModel):
    id: str
    title: str
    provider: str
    model: str | None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse] = Field(default_factory=list)


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=12000)
    client_message_id: str | None = None


class ChatResponse(BaseModel):
    conversation: ConversationResponse
    message: MessageResponse | None = None
    approval_required: bool | None = None
    pending_tool_call: dict | None = None
    approval_id: str | None = None
    agent_loop: dict | None = None
    clarification_required: bool | None = None
    clarification_session_id: str | None = None
    clarification_questions: list[dict] | None = None
    # Operator Request Pipeline
    operator_request_id: str | None = None
    state: str | None = None
    operation_type: str | None = None
    readiness_state: str | None = None


class PullModelRequest(BaseModel):
    model: str = Field(min_length=1)


class PullStatusResponse(BaseModel):
    active: bool
    model: str | None = None
    status: str | None = None
    phase: str | None = None
    completed: int | None = None
    total: int | None = None
    percentage: float | None = None
    message: str | None = None
    error: dict | None = None
    started_at: str | None = None
    finished_at: str | None = None


class OllamaStorageResponse(BaseModel):
    location: str | None = None
    drive: str | None = None
    available_bytes: int | None = None
    used_bytes: int | None = None


class OllamaProcessOperationResponse(BaseModel):
    success: bool
    already_running: bool | None = None
    state: str
    managed: bool
    managed_pid: int | None = None
    code: str | None = None
    message: str


class OllamaProcessStatusResponse(BaseModel):
    state: str
    running: bool
    managed: bool
    managed_pid: int | None = None
    executable_path: str | None = None
    base_url: str = "http://127.0.0.1:11434"
    storage_location: str | None = None


class ApprovalApproveRequest(BaseModel):
    decision_reason: str | None = Field(None, description="Optional user-provided reason")


class ApprovalRejectRequest(BaseModel):
    decision_reason: str | None = Field(None, description="Optional user-provided reason")


class ApprovalResponse(BaseModel):
    approval_id: str
    status: str
    tool_id: str
    action: str
    tool_call_id: str
    arguments: dict
    conversation_id: str
    request_id: str | None = None
    created_at: datetime
    expires_at: datetime
    executed_at: datetime | None = None
    decision_reason: str | None = None
    execution_result: dict | None = None


class PendingApprovalListResponse(BaseModel):
    approvals: list[ApprovalResponse]
    total: int
    limit: int = 50
    offset: int = 0



