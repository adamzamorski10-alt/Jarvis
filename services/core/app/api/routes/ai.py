"""AI provider and assistant routes."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.ai.services.ai_service import AIService, AIUnavailable
from app.ai.services.conversation_service import ConversationNotFound, ConversationService
from app.ai.services.provider_manager import AIConfigError, ProviderManager
from app.api.schemas.ai import (
    AIConfigResponse,
    AIModelResponse,
    AIProviderItem,
    AIStatusResponse,
    ChatRequest,
    ChatResponse,
    ConversationResponse,
    OllamaProcessOperationResponse,
    OllamaProcessStatusResponse,
    OllamaStorageResponse,
    PullModelRequest,
    PullStatusResponse,
    UpdateAIConfigRequest,
)
from app.dependencies import get_ai_service, get_conversation_service, get_provider_manager, get_activity_service
from app.identity.auth import get_request_context, require_authenticated
from app.services.activity_service import ActivityService

router = APIRouter(prefix="/ai")


def conversation_to_response(conversation) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        provider=conversation.provider,
        model=conversation.model,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            {
                "id": message.id,
                "conversation_id": message.conversation_id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
            }
            for message in getattr(conversation, "messages", [])
        ],
    )


@router.get("/providers", response_model=list[AIProviderItem])
async def providers(manager: ProviderManager = Depends(get_provider_manager)):
    return await manager.list_supported_providers()


@router.get("/config", response_model=AIConfigResponse)
async def get_config(
    request: Request,
    manager: ProviderManager = Depends(get_provider_manager),
):
    _ = require_authenticated(request)
    return await manager.get_config()


@router.put("/config", response_model=AIConfigResponse)
async def update_config(
    request: Request,
    req: UpdateAIConfigRequest,
    manager: ProviderManager = Depends(get_provider_manager),
    activity: ActivityService = Depends(get_activity_service),
):
    _ = require_authenticated(request)
    try:
        before = await manager.get_config()
        config = await manager.update_config(
            req.provider,
            req.ollama_url,
            req.selected_model,
            req.kimi_base_url,
            req.kimi_api_key,
            req.openrouter_base_url,
            req.openrouter_api_key,
        )
    except AIConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if before["provider"] != config["provider"]:
        await activity.create_event("AI provider changed", category="ai", details={"provider": config["provider"]})
    await activity.create_event(
        "AI configuration updated",
        category="ai",
        details={"provider": config["provider"], "model_selected": bool(config["selected_model"])},
    )
    if config["selected_model"]:
        await activity.create_event("AI model selected", category="ai", details={"provider": config["provider"]})
    return config


@router.get("/status", response_model=AIStatusResponse)
async def status(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    return await ai.get_status()


@router.post("/test-connection", response_model=AIStatusResponse)
async def test_connection(
    request: Request,
    ai: AIService = Depends(get_ai_service),
    activity: ActivityService = Depends(get_activity_service),
):
    _ = require_authenticated(request)
    result = await ai.get_status()
    provider_name = str(result["provider"]).title()
    await activity.create_event(
        f"{provider_name} connection successful" if result["ai_ready"] else f"{provider_name} connection failed",
        category="ai",
        level="INFO" if result["ai_ready"] else "WARNING",
        details={"provider": result["provider"], "status": result["provider_status"], "model_status": result["model_status"]},
    )
    return result


@router.get("/models", response_model=list[AIModelResponse])
async def models(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    try:
        return await ai.list_models()
    except AIUnavailable as exc:
        raise HTTPException(status_code=503, detail={"message": str(exc), "status": exc.status}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/models/refresh", response_model=list[AIModelResponse])
async def refresh_models(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    return await models(request, ai)


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    request: Request,
    conversations: ConversationService = Depends(get_conversation_service),
):
    _ = require_authenticated(request)
    return [conversation_to_response(item) for item in await conversations.list()]


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    request: Request,
    manager: ProviderManager = Depends(get_provider_manager),
    conversations: ConversationService = Depends(get_conversation_service),
    activity: ActivityService = Depends(get_activity_service),
):
    _ = require_authenticated(request)
    config = await manager.get_config()
    conversation = await conversations.create(str(config["provider"]), config["selected_model"])
    await activity.create_event("Conversation created", category="ai")
    return conversation_to_response(conversation)


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    request: Request,
    conversation_id: str,
    conversations: ConversationService = Depends(get_conversation_service),
):
    _ = require_authenticated(request)
    try:
        return conversation_to_response(await conversations.get(conversation_id))
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    req: ChatRequest,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    ctx = get_request_context(request)
    try:
        result = await ai.chat(
            req.conversation_id,
            req.message,
            req.client_message_id,
            actor_id=ctx.actor_id,
            request_context=ctx,
        )
        result["conversation"] = await ai._conversations.get(result["conversation"].id)
        response: dict = {
            "conversation": conversation_to_response(result["conversation"]),
        }
        if result.get("message"):
            response["message"] = {
                "id": result["message"].id,
                "conversation_id": result["message"].conversation_id,
                "role": result["message"].role,
                "content": result["message"].content,
                "created_at": result["message"].created_at,
            }
        else:
            response["message"] = None
        if result.get("approval_required"):
            response["approval_required"] = True
            response["pending_tool_call"] = result.get("pending_tool_call")
            response["approval_id"] = result.get("approval_id")
        if result.get("clarification_required"):
            response["clarification_required"] = True
            response["clarification_session_id"] = result.get("clarification_session_id")
            response["clarification_questions"] = result.get("clarification_questions")
        if result.get("agent_loop"):
            response["agent_loop"] = result.get("agent_loop")
        # Operator Request Pipeline fields
        if result.get("operator_request"):
            operator_request = result["operator_request"]
            response["operator_request_id"] = operator_request.request_id
            response["state"] = operator_request.state.value
            response["operation_type"] = operator_request.operation_type
            response["readiness_state"] = operator_request.readiness_state.value
        return response
    except ConversationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"message": str(exc), "status": "not_found", "error": {"code": "conversation_not_found", "message": str(exc), "retryable": False}},
        ) from exc
    except AIUnavailable as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "status": exc.code,
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "provider": exc.provider,
                    "retryable": exc.retryable,
                },
            },
        ) from exc


@router.post("/models/pull", response_model=PullStatusResponse)
async def pull_model(
    request: Request,
    req: PullModelRequest,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    try:
        return await ai.pull_model(req.model)
    except AIUnavailable as exc:
        status_code = 409 if exc.code in ("model_download_in_progress", "insufficient_disk_space") else 400
        detail = {
            "message": str(exc),
            "status": exc.code,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "provider": exc.provider or "ollama",
                "retryable": exc.retryable,
                **exc.details,
            },
        }
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/models/pull/status", response_model=PullStatusResponse)
async def get_pull_status(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    return await ai.get_pull_status()


@router.post("/models/pull/cancel", response_model=PullStatusResponse)
async def cancel_pull(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    return await ai.cancel_pull()


@router.get("/models/storage", response_model=OllamaStorageResponse)
async def get_storage_info(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    return await ai.get_storage_info()


@router.delete("/models/{model_name:path}")
async def delete_model(
    request: Request,
    model_name: str,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    try:
        return await ai.delete_model(model_name)
    except AIUnavailable as exc:
        detail = {
            "message": str(exc),
            "status": exc.code,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "provider": exc.provider or "ollama",
                "retryable": exc.retryable,
            },
        }
        raise HTTPException(status_code=400, detail=detail) from exc


@router.post("/ollama/start", response_model=OllamaProcessOperationResponse)
async def start_ollama(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    try:
        return await ai.start_ollama_process()
    except AIUnavailable as exc:
        status_code = 409 if exc.code in ("ollama_startup_timeout", "ollama_process_crashed", "ollama_start_failed") else 400
        detail = {
            "message": str(exc),
            "status": exc.code,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "provider": "ollama",
                "retryable": exc.retryable,
            },
        }
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/ollama/stop", response_model=OllamaProcessOperationResponse)
async def stop_ollama(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    try:
        return await ai.stop_ollama_process()
    except AIUnavailable as exc:
        status_code = 409 if exc.code == "not_managed" else 400
        detail = {
            "message": str(exc),
            "status": exc.code,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "provider": "ollama",
                "retryable": False,
            },
        }
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/ollama/restart", response_model=OllamaProcessOperationResponse)
async def restart_ollama(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    try:
        return await ai.restart_ollama_process()
    except AIUnavailable as exc:
        status_code = 409 if exc.code in ("not_managed", "ollama_startup_timeout", "ollama_process_crashed", "ollama_start_failed") else 400
        detail = {
            "message": str(exc),
            "status": exc.code,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "provider": "ollama",
                "retryable": exc.retryable,
            },
        }
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.get("/ollama/process", response_model=OllamaProcessStatusResponse)
async def get_ollama_process(
    request: Request,
    ai: AIService = Depends(get_ai_service),
):
    _ = require_authenticated(request)
    return await ai.get_ollama_process_status()




