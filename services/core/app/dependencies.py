"""
FastAPI Dependency Injection
Provides reusable dependencies for routes.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any, AsyncGenerator
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.services.settings_service import SettingsService
from app.services.activity_service import ActivityService
from app.ai.services.provider_manager import ProviderManager
from app.ai.services.conversation_service import ConversationService
from app.ai.services.ai_service import AIService
from app.context_assembly.service import ContextAssemblyService
from app.information_sufficiency.service import InformationSufficiencyService
from app.clarification.service import ClarificationService
from app.operator_request.service import OperatorRequestService
from app.operator_capability.resolver import CapabilityResolver
from app.operator_capability.executor import OperatorCapabilityExecutor

if TYPE_CHECKING:
    from app.repositories.grant_repository import GrantRepository
    from app.repositories.audit_log_repository import AuditLogRepository
    from app.repositories.approval_repository import ApprovalRepository
    from app.tools.audit_service import AuditLogService
    from app.tools.executor import ToolExecutor
    from app.tools.implementation_registry import ToolImplementationRegistry
    from app.tools.registry import ToolRegistry
    from app.tools.permission_engine import PermissionEngine
    from app.tools.tool_execution_service import ToolExecutionService
    from app.tools.approval_service import ApprovalService


async def get_settings_service(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[SettingsService, None]:
    yield SettingsService(session)


async def get_activity_service(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[ActivityService, None]:
    yield ActivityService(session)


async def get_provider_manager(
    settings_svc: SettingsService = Depends(get_settings_service),
) -> AsyncGenerator[ProviderManager, None]:
    yield ProviderManager(settings_svc)


async def get_conversation_service(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[ConversationService, None]:
    yield ConversationService(session)


def get_tool_registry():
    from app.tools.registry import tool_registry
    return tool_registry


async def get_grant_repository(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[GrantRepository, None]:
    from app.repositories.grant_repository import GrantRepository
    yield GrantRepository(session)


async def get_approval_repository(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[ApprovalRepository, None]:
    from app.repositories.approval_repository import ApprovalRepository
    yield ApprovalRepository(session)


async def get_audit_log_repository(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[AuditLogRepository, None]:
    from app.repositories.audit_log_repository import AuditLogRepository
    yield AuditLogRepository(session)


async def get_audit_log_service(
    repo: AuditLogRepository = Depends(get_audit_log_repository),
) -> AsyncGenerator[AuditLogService, None]:
    from app.tools.audit_service import AuditLogService
    yield AuditLogService(repo)


async def get_permission_engine(
    registry: ToolRegistry = Depends(get_tool_registry),
    grant_repo: GrantRepository = Depends(get_grant_repository),
    audit_service: AuditLogService = Depends(get_audit_log_service),
    approval_repo: ApprovalRepository = Depends(get_approval_repository),
) -> AsyncGenerator[PermissionEngine, None]:
    from app.tools.permission_engine import PermissionEngine
    yield PermissionEngine(
        registry=registry,
        grant_repo=grant_repo,
        audit_service=audit_service,
        approval_repo=approval_repo,
    )


def get_tool_executor(
    registry: ToolRegistry = Depends(get_tool_registry),
) -> ToolExecutor:
    from app.tools.executor import ToolExecutor
    from app.tools.implementation_registry import tool_implementation_registry
    return ToolExecutor(registry=registry, impl_registry=tool_implementation_registry)


def get_tool_execution_service(
    registry: ToolRegistry = Depends(get_tool_registry),
    permission_engine: PermissionEngine = Depends(get_permission_engine),
    audit_service: AuditLogService = Depends(get_audit_log_service),
    executor: ToolExecutor = Depends(get_tool_executor),
) -> ToolExecutionService:
    from app.tools.implementation_registry import tool_implementation_registry
    from app.tools.tool_execution_service import ToolExecutionService
    return ToolExecutionService(
        registry=registry,
        impl_registry=tool_implementation_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=executor,
    )


def get_approval_service(
    repo: ApprovalRepository = Depends(get_approval_repository),
    tool_execution_service: ToolExecutionService = Depends(get_tool_execution_service),
    settings_svc: SettingsService = Depends(get_settings_service),
    audit_service: AuditLogService = Depends(get_audit_log_service),
) -> ApprovalService:
    from app.tools.approval_service import ApprovalService
    return ApprovalService(
        repo=repo,
        tool_execution_service=tool_execution_service,
        settings=settings_svc,
        audit_service=audit_service,
    )


async def get_clarification_service(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[ClarificationService | None, None]:
    from app.clarification.repository import ClarificationRepository
    from app.clarification.service import ClarificationService
    repo = ClarificationRepository(session)
    yield ClarificationService(repo)


async def get_context_assembly_service(
    memory_service: Any | None = None,
) -> AsyncGenerator[ContextAssemblyService | None, None]:
    from app.context_assembly.service import ContextAssemblyService
    yield ContextAssemblyService(memory_service=memory_service)


async def get_information_sufficiency_service(
    llm_provider: Any | None = None,
) -> AsyncGenerator[InformationSufficiencyService | None, None]:
    from app.information_sufficiency.service import InformationSufficiencyService
    yield InformationSufficiencyService(llm_provider=llm_provider)


async def get_operator_request_service(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[OperatorRequestService | None, None]:
    yield OperatorRequestService()


async def get_task_repository(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[Any, None]:
    from app.repositories.task_repository import TaskRepository
    yield TaskRepository(session)


async def get_plan_manager(
    providers: ProviderManager = Depends(get_provider_manager),
):
    from app.manager.plan_manager import PlanManager
    from app.manager.providers.configured import ConfiguredPlanProvider

    config = await providers.get_config()
    selected_model = config.get("selected_model")
    if not selected_model:
        return None
    provider = await providers.get_provider()
    return PlanManager(
        provider=ConfiguredPlanProvider(provider=provider, model=str(selected_model)),
    )


async def get_manager_planning_service(
    plan_manager = Depends(get_plan_manager),
):
    if plan_manager is None:
        return None
    from app.ai_manager.bridge import ManagerPlanningService
    from app.ai_manager.gate import PolicyCapabilityGate
    return ManagerPlanningService(
        plan_manager=plan_manager,
        policy_gate=PolicyCapabilityGate(),
    )


async def get_plan_service(
    task_repository = Depends(get_task_repository),
):
    from app.services.plan_service import PlanService
    return PlanService(task_repository)


async def get_web_search_service(
    providers: ProviderManager = Depends(get_provider_manager),
    audit_service: AuditLogService = Depends(get_audit_log_service),
):
    from app.web_search.service import WebSearchService
    try:
        provider = await providers.get_web_search_provider()
    except Exception:
        provider = None
    return WebSearchService(provider=provider, audit_service=audit_service) if provider is not None else None


async def get_execution_bridge(
    session: AsyncSession = Depends(get_db),
    task_repository = Depends(get_task_repository),
    manager_planning_service = Depends(get_manager_planning_service),
    approval_service = Depends(get_approval_service),
):
    """
    Build the single application-owned bridge from AI Manager decisions to
    the existing autonomous development execution core.

    This dependency deliberately constructs the same real execution stack used
    by the autonomous plan control-plane route. No fake agent or shortcut is
    allowed here.
    """
    from pathlib import Path
    import subprocess

    from app.ai_manager.approval_repository import CapabilityApprovalRepository
    from app.ai_manager.approval_service import CapabilityApprovalService
    from app.ai_manager.execution_bridge import ExecutionBridge
    from app.ai_manager.gate import PolicyCapabilityGate
    from app.orchestration.agent_factory import DevelopmentAgentFactory
    from app.orchestration.orchestrator import AutonomousTaskOrchestrator
    from app.orchestration.run_controller import AutonomousRunController
    from app.repositories.approval_repository import ApprovalRepository
    from app.repositories.audit_log_repository import AuditLogRepository
    from app.repositories.grant_repository import GrantRepository
    from app.services.autonomous_loop import AutonomousPlanExecutor
    from app.services.plan_execution_service import PlanExecutionService
    from app.services.settings_service import SettingsService
    from app.tools.audit_service import AuditLogService
    from app.tools.executor import ToolExecutor
    from app.tools.implementation_registry import tool_implementation_registry
    from app.tools.permission_engine import PermissionEngine
    from app.tools.registry import tool_registry
    from app.tools.tool_execution_service import ToolExecutionService
    from app.verification.check_registry import CheckRegistry
    from app.verification.gate import VerificationGate
    from app.workspace.workspace_manager import WorkspaceManager
    from app.ai.services.provider_manager import ProviderManager

    repo_root = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            repo_root = Path(result.stdout.strip()).resolve()
    except Exception:
        pass

    audit_repo = AuditLogRepository(session)
    audit_service = AuditLogService(audit_repo)
    grant_repo = GrantRepository(session)
    approval_repo = ApprovalRepository(session)
    permission_engine = PermissionEngine(
        registry=tool_registry,
        grant_repo=grant_repo,
        audit_service=audit_service,
        approval_repo=approval_repo,
    )
    tool_executor = ToolExecutor(
        registry=tool_registry,
        impl_registry=tool_implementation_registry,
    )
    tool_service = ToolExecutionService(
        registry=tool_registry,
        impl_registry=tool_implementation_registry,
        permission_engine=permission_engine,
        audit_service=audit_service,
        executor=tool_executor,
    )

    settings_service = SettingsService(session)
    provider_manager = ProviderManager(settings_service)
    agent_factory = DevelopmentAgentFactory(
        provider_manager=provider_manager,
        tool_execution_service=tool_service,
    )
    try:
        development_agent = await agent_factory.create_agent()
    except Exception as exc:
        raise RuntimeError("Development agent could not be initialized") from exc

    from app.tasks.state_machine import TaskStateMachine
    state_machine = TaskStateMachine(task_repository)
    workspace_manager = WorkspaceManager(repo_root=repo_root)
    verification_gate = VerificationGate(
        registry=CheckRegistry(),
        primary_worktree_path=repo_root,
    )
    orchestrator = AutonomousTaskOrchestrator(
        task_repo=task_repository,
        state_machine=state_machine,
        workspace_manager=workspace_manager,
        verification_gate=verification_gate,
        agent=development_agent,
    )
    run_controller = AutonomousRunController(
        task_repo=task_repository,
        state_machine=state_machine,
        workspace_manager=workspace_manager,
        verification_gate=verification_gate,
        orchestrator=orchestrator,
    )
    plan_execution_service = PlanExecutionService(
        task_repository=task_repository,
        run_controller=run_controller,
    )
    autonomous_executor = AutonomousPlanExecutor(
        task_repository=task_repository,
        plan_execution_service=plan_execution_service,
        audit_service=audit_service,
    )

    capability_approval_service = CapabilityApprovalService(
        repo=CapabilityApprovalRepository(session),
        audit_service=audit_service,
    )

    from app.services.plan_service import PlanService
    plan_service = PlanService(task_repository)

    return ExecutionBridge(
        planning_service=manager_planning_service,
        plan_execution_service=plan_execution_service,
        autonomous_executor=autonomous_executor,
        capability_gate=PolicyCapabilityGate(),
        approval_service=capability_approval_service,
        plan_service=plan_service,
        audit_service=audit_service,
    )


async def get_execution_bridge_factory(
    session: AsyncSession = Depends(get_db),
    task_repository = Depends(get_task_repository),
    manager_planning_service = Depends(get_manager_planning_service),
    approval_service: ApprovalService = Depends(get_approval_service),
):
    """Return a lazy factory so read-only AI endpoints do not build the coding stack."""
    async def factory():
        return await get_execution_bridge(
            session=session,
            task_repository=task_repository,
            manager_planning_service=manager_planning_service,
            approval_service=approval_service,
        )

    return factory


async def get_web_research_components(
    providers: ProviderManager = Depends(get_provider_manager),
    web_search_service = Depends(get_web_search_service),
):
    """Provide request-scoped bounded research components; close the HTTP client afterward."""
    if web_search_service is None:
        yield (None, None)
        return

    config = await providers.get_config()
    selected_model = config.get("selected_model")
    if not selected_model:
        yield (None, None)
        return

    from app.web_research.fetcher import PublicPageFetcher
    from app.web_research.service import ResearchService
    from app.web_research.synthesis import ResearchSynthesisAdapter

    fetcher = PublicPageFetcher()
    try:
        try:
            provider = await providers.get_provider()
            components = (
                ResearchService(web_search_service, fetcher),
                ResearchSynthesisAdapter(provider, model=str(selected_model)),
            )
        except Exception:
            # Research is optional: a synthesis-provider failure must not take
            # down ordinary chat or the existing web-search capability.
            components = (None, None)
        yield components
    finally:
        await fetcher.aclose()


async def get_ai_service(
    task_repository = Depends(get_task_repository),
    providers: ProviderManager = Depends(get_provider_manager),
    conversations: ConversationService = Depends(get_conversation_service),
    activity: ActivityService = Depends(get_activity_service),
    settings_svc: SettingsService = Depends(get_settings_service),
    tool_execution_service: ToolExecutionService = Depends(get_tool_execution_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    context_assembly_service: ContextAssemblyService | None = Depends(get_context_assembly_service),
    information_sufficiency_service: InformationSufficiencyService | None = Depends(get_information_sufficiency_service),
    clarification_service: ClarificationService | None = Depends(get_clarification_service),
    operator_request_service: OperatorRequestService | None = Depends(get_operator_request_service),
    manager_planning_service = Depends(get_manager_planning_service),
    plan_service = Depends(get_plan_service),
    execution_bridge_factory = Depends(get_execution_bridge_factory),
    web_search_service = Depends(get_web_search_service),
    research_components = Depends(get_web_research_components),
    permission_engine: PermissionEngine = Depends(get_permission_engine),
) -> AsyncGenerator[AIService, None]:
    capability_resolver = CapabilityResolver()
    web_research_service, research_synthesis_adapter = research_components
    from app.tasks.state_machine import TaskStateMachine
    task_state_machine = TaskStateMachine(task_repository)
    capability_executor = OperatorCapabilityExecutor(
        tool_execution_service=tool_execution_service,
        task_repository=task_repository,
        task_state_machine=task_state_machine,
        manager_planning_service=manager_planning_service,
        plan_service=plan_service,
        execution_bridge_factory=execution_bridge_factory,
        web_search_service=web_search_service,
        web_research_service=web_research_service,
        research_synthesis_adapter=research_synthesis_adapter,
        permission_engine=permission_engine,
        autonomy_mode=None,
    )
    yield AIService(
        providers,
        conversations,
        activity,
        settings_svc,
        tool_execution_service,
        approval_service,
        context_assembly_service=context_assembly_service,
        information_sufficiency_service=information_sufficiency_service,
        clarification_service=clarification_service,
        operator_request_service=operator_request_service,
        capability_resolver=capability_resolver,
        capability_executor=capability_executor,
    )


async def get_history_service(
    session: AsyncSession = Depends(get_db),
    task_repo: TaskRepository = Depends(get_task_repository),
) -> AsyncGenerator[HistoryService, None]:
    from app.events.history import HistoryService
    from app.events.repository import EventRepository
    yield HistoryService(event_repository=EventRepository(session), task_repository=task_repo)


async def get_scheduler_service(
    session: AsyncSession = Depends(get_db),
    task_repo: TaskRepository = Depends(get_task_repository),
) -> AsyncGenerator[SchedulerService, None]:
    from app.events.emitter import EventEmitter
    from app.events.repository import EventRepository
    from app.notifications.policy import NotificationPolicy
    from app.notifications.repository import NotificationRepository
    from app.scheduler.repository import SchedulerRepository
    from app.scheduler.service import SchedulerService
    event_repo = EventRepository(session)
    notification_policy = NotificationPolicy()
    notification_repo = NotificationRepository(session)
    emitter = EventEmitter(event_repo, notification_policy=notification_policy, notification_repository=notification_repo)
    repo = SchedulerRepository(session, event_emitter=emitter)
    yield SchedulerService(repo, task_repo=task_repo)


async def get_memory_service(
    session: AsyncSession = Depends(get_db),
) -> AsyncGenerator[MemoryService, None]:
    from app.events.emitter import EventEmitter
    from app.events.repository import EventRepository
    from app.memory.service import MemoryService
    event_repo = EventRepository(session)
    emitter = EventEmitter(event_repo)
    yield MemoryService(session, event_emitter=emitter)


def get_background_execution_manager():
    from app.services.background_execution_manager import background_execution_manager
    return background_execution_manager


def get_session_store():
    from app.main import _session_store
    if _session_store is None:
        raise RuntimeError("Session store not initialized")
    return _session_store


def get_bootstrap_secret():
    from app.main import _bootstrap_secret
    if _bootstrap_secret is None:
        raise RuntimeError("Bootstrap secret not initialized")
    return _bootstrap_secret

