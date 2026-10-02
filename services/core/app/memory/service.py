"""
Memory Service — Application-level authority and business rules.

Owns Memory application semantics:
  - caller authority enforcement
  - trust/provenance assignment
  - scope validation
  - lifecycle enforcement
  - supersession orchestration
  - safe retrieval/listing semantics
  - event emission for audit

MemoryRepository remains persistence-only.
MemoryService must NOT grant execution authority or modify non-Memory state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.schemas import EventType
from app.memory.models import (
    MemoryCategory,
    MemoryEntry,
    MemoryScope,
    ProvenanceSource,
    TrustLevel,
)
from app.memory.repository import MemoryRepository


# ── Caller Identity ────────────────────────────────────────────────────────────


class CallerType(str, Enum):
    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"


@dataclass(frozen=True)
class MemoryCaller:
    """
    Narrow caller identity for Memory authority checks.

    No authentication, no full identity system — just enough to enforce
    the contract's write-authority matrix.
    """
    caller_type: CallerType
    caller_id: str | None = None

    @property
    def is_user(self) -> bool:
        return self.caller_type == CallerType.USER

    @property
    def is_system(self) -> bool:
        return self.caller_type == CallerType.SYSTEM

    @property
    def is_agent(self) -> bool:
        return self.caller_type == CallerType.AGENT


# ── Exceptions ─────────────────────────────────────────────────────────────────


class MemoryError(ValueError):
    """Base Memory service error."""
    pass


class MemoryNotFound(MemoryError):
    pass


class MemoryForbidden(MemoryError):
    """Caller lacks authority for the requested operation."""
    pass


class MemoryConflict(MemoryError):
    """Lifecycle conflict (e.g., supersede a deleted memory)."""
    pass


class InvalidProvenance(MemoryError):
    """Caller attempted to manufacture prohibited provenance."""
    pass


# ── Authority defaults ─────────────────────────────────────────────────────────

# Default trust level per category when caller does not specify one.
# For contextual, system callers get SYSTEM trust (they are the system).
_CATEGORY_DEFAULT_TRUST: dict[MemoryCategory, TrustLevel] = {
    MemoryCategory.USER_INSTRUCTION: TrustLevel.EXPLICIT_USER,
    MemoryCategory.PROJECT_KNOWLEDGE: TrustLevel.DERIVED,
    MemoryCategory.SYSTEM_KNOWLEDGE: TrustLevel.SYSTEM,
    MemoryCategory.AGENT_OBSERVATION: TrustLevel.AGENT,
    MemoryCategory.DECISION_RATIONALE: TrustLevel.DERIVED,
    MemoryCategory.CONTEXTUAL: TrustLevel.AGENT,
}

# Default source per category when caller does not specify one.
_CATEGORY_DEFAULT_SOURCE: dict[MemoryCategory, ProvenanceSource] = {
    MemoryCategory.USER_INSTRUCTION: ProvenanceSource.USER,
    MemoryCategory.PROJECT_KNOWLEDGE: ProvenanceSource.EVENT,
    MemoryCategory.SYSTEM_KNOWLEDGE: ProvenanceSource.SYSTEM,
    MemoryCategory.AGENT_OBSERVATION: ProvenanceSource.AGENT,
    MemoryCategory.DECISION_RATIONALE: ProvenanceSource.EVENT,
    MemoryCategory.CONTEXTUAL: ProvenanceSource.AGENT,
}

# Categories each caller type is permitted to create.
_CREATOR_ALLOWED_CATEGORIES: dict[CallerType, frozenset[MemoryCategory]] = {
    CallerType.USER: frozenset({
        MemoryCategory.USER_INSTRUCTION,
        MemoryCategory.PROJECT_KNOWLEDGE,
        MemoryCategory.SYSTEM_KNOWLEDGE,
        MemoryCategory.AGENT_OBSERVATION,
        MemoryCategory.DECISION_RATIONALE,
        MemoryCategory.CONTEXTUAL,
    }),
    CallerType.SYSTEM: frozenset({
        MemoryCategory.SYSTEM_KNOWLEDGE,
        MemoryCategory.CONTEXTUAL,
    }),
    CallerType.AGENT: frozenset({
        MemoryCategory.AGENT_OBSERVATION,
        MemoryCategory.DECISION_RATIONALE,
        MemoryCategory.CONTEXTUAL,
    }),
}

# Trust levels each caller type is permitted to manufacture.
_CREATOR_ALLOWED_TRUST: dict[CallerType, frozenset[TrustLevel]] = {
    CallerType.USER: frozenset({
        TrustLevel.EXPLICIT_USER,
        TrustLevel.SYSTEM,
        TrustLevel.DERIVED,
        TrustLevel.EXTERNAL,
        TrustLevel.AGENT,
    }),
    CallerType.SYSTEM: frozenset({
        TrustLevel.SYSTEM,
        TrustLevel.DERIVED,
        TrustLevel.EXTERNAL,
    }),
    CallerType.AGENT: frozenset({
        TrustLevel.AGENT,
        TrustLevel.DERIVED,
    }),
}

# Source values each caller type is permitted to claim.
_CREATOR_ALLOWED_SOURCE: dict[CallerType, frozenset[ProvenanceSource]] = {
    CallerType.USER: frozenset({
        ProvenanceSource.USER,
        ProvenanceSource.SYSTEM,
        ProvenanceSource.EVENT,
        ProvenanceSource.TASK,
        ProvenanceSource.AGENT,
        ProvenanceSource.EXTERNAL,
    }),
    CallerType.SYSTEM: frozenset({
        ProvenanceSource.SYSTEM,
        ProvenanceSource.EVENT,
        ProvenanceSource.TASK,
        ProvenanceSource.EXTERNAL,
    }),
    CallerType.AGENT: frozenset({
        ProvenanceSource.AGENT,
        ProvenanceSource.EVENT,
        ProvenanceSource.TASK,
    }),
}


# ── Service ────────────────────────────────────────────────────────────────────


class MemoryService:
    """
    Application-level Memory service.

    Enforces contract-level authority, trust, provenance, scope, and lifecycle
    rules. Delegates persistence to MemoryRepository.
    """

    def __init__(self, session: AsyncSession, event_emitter: Any = None) -> None:
        self._repo = MemoryRepository(session)
        self._event_emitter = event_emitter

    async def create(
        self,
        caller: MemoryCaller,
        category: MemoryCategory,
        scope: MemoryScope,
        content: dict[str, Any],
        trust_level: TrustLevel | None = None,
        source: ProvenanceSource | None = None,
        source_id: str | None = None,
        scope_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        confidence: float = 1.0,
    ) -> MemoryEntry:
        """
        Create a new memory entry.

        Caller must be authorized for the requested category/trust/source.
        Trust and source default to category-appropriate values when omitted.
        """
        resolved_trust = trust_level or _CATEGORY_DEFAULT_TRUST[category]
        if category == MemoryCategory.CONTEXTUAL and trust_level is None:
            if caller.is_system:
                resolved_trust = TrustLevel.SYSTEM
            elif caller.is_agent:
                resolved_trust = TrustLevel.AGENT
            else:
                resolved_trust = TrustLevel.EXPLICIT_USER

        resolved_source = source or _CATEGORY_DEFAULT_SOURCE[category]
        if category == MemoryCategory.CONTEXTUAL and source is None:
            if caller.is_system:
                resolved_source = ProvenanceSource.SYSTEM
            elif caller.is_agent:
                resolved_source = ProvenanceSource.AGENT
            else:
                resolved_source = ProvenanceSource.USER

        resolved_source_id = source_id
        if resolved_source_id is None and resolved_source in (
            ProvenanceSource.USER,
            ProvenanceSource.SYSTEM,
            ProvenanceSource.AGENT,
        ):
            if caller.caller_id is None:
                raise InvalidProvenance(
                    f"caller_id is required to default source_id for source '{resolved_source.value}'"
                )
            resolved_source_id = caller.caller_id

        self._enforce_can_create(caller, category, resolved_trust, resolved_source)

        now = datetime.now(timezone.utc)
        entry = MemoryEntry(
            category=category,
            scope=scope,
            scope_id=scope_id,
            content=content,
            trust_level=resolved_trust,
            confidence=confidence,
            source=resolved_source,
            source_id=resolved_source_id,
            created_at=now,
            updated_at=now,
            archived_at=None,
            expires_at=None,
            superseded_by=None,
            deleted_at=None,
            metadata=metadata,
        )
        persisted = await self._repo.create(entry)
        await self._emit_memory_event(EventType.MEMORY_CREATED, persisted, caller=caller)
        return persisted

    async def get(self, memory_id: str, include_deleted: bool = False) -> MemoryEntry | None:
        """Retrieve a memory by ID. Active only unless include_deleted=True."""
        if include_deleted:
            return await self._repo.get_including_deleted(memory_id)
        return await self._repo.get(memory_id)

    async def list(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        category: MemoryCategory | None = None,
        trust_level: TrustLevel | None = None,
        source: ProvenanceSource | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[MemoryEntry], int]:
        """List memories with optional filters. Returns (entries, total_count)."""
        return await self._repo.list(
            scope=scope,
            scope_id=scope_id,
            category=category,
            trust_level=trust_level,
            source=source,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
        )

    async def update(
        self,
        caller: MemoryCaller,
        memory_id: str,
        changes: dict[str, Any],
    ) -> MemoryEntry | None:
        """
        Update mutable fields of an existing memory.

        Immutable fields (memory_id, category, scope, scope_id, trust_level,
        source, source_id, created_at) cannot be changed.
        """
        existing = await self._repo.get(memory_id)
        if existing is None:
            raise MemoryNotFound(f"Memory {memory_id} not found or already deleted")

        self._enforce_can_update(caller, existing)

        allowed = {"content", "confidence", "metadata", "archived_at", "expires_at"}
        forbidden = set(changes.keys()) - allowed
        if forbidden:
            raise MemoryError(f"Field(s) not allowed in updates: {sorted(forbidden)}")

        if "scope" in changes or "scope_id" in changes:
            raise MemoryError("Scope and scope_id are immutable after creation")

        if "trust_level" in changes:
            raise MemoryError("trust_level is immutable after creation")

        if "source" in changes or "source_id" in changes:
            raise MemoryError("source and source_id are immutable after creation")

        if "category" in changes:
            raise MemoryError("category is immutable after creation")

        updated = await self._repo.update(memory_id, changes)
        if updated is not None:
            await self._emit_memory_event(
                EventType.MEMORY_UPDATED,
                updated,
                caller=caller,
                changed_fields=sorted(changes.keys()),
            )
        return updated

    async def delete(self, caller: MemoryCaller, memory_id: str) -> bool:
        """
        Soft-delete a memory.

        Returns True if the memory was found and deleted.
        """
        existing = await self._repo.get(memory_id)
        if existing is None:
            return False

        self._enforce_can_delete(caller, existing)
        result = await self._repo.delete(memory_id)
        if result:
            await self._emit_memory_event(
                EventType.MEMORY_DELETED, existing, caller=caller
            )
        return result

    async def supersede(
        self,
        caller: MemoryCaller,
        memory_id: str,
        new_content: dict[str, Any],
        new_metadata: dict[str, Any] | None = None,
        new_confidence: float = 1.0,
    ) -> MemoryEntry:
        """
        Supersede an existing memory with a new entry.

        Creates the new memory, marks the old as superseded, and returns
        the new entry. Atomic at repository level.
        """
        existing = await self._repo.get(memory_id)
        if existing is None:
            raise MemoryNotFound(f"Memory {memory_id} not found or already deleted")

        self._enforce_can_supersede(caller, existing)

        new_entry = MemoryEntry(
            category=existing.category,
            scope=existing.scope,
            scope_id=existing.scope_id,
            content=new_content,
            trust_level=existing.trust_level,
            confidence=new_confidence,
            source=existing.source,
            source_id=existing.source_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            archived_at=None,
            expires_at=None,
            superseded_by=None,
            deleted_at=None,
            metadata=new_metadata,
        )
        persisted = await self._repo.supersede(memory_id, new_entry)
        await self._emit_memory_event(
            EventType.MEMORY_SUPERSEDED,
            persisted,
            caller=caller,
            superseded_by=persisted.memory_id,
            correlation_id=memory_id,
            memory_id=memory_id,
        )
        return persisted

    async def _emit_memory_event(
        self,
        event_type: EventType,
        memory: MemoryEntry,
        *,
        caller: MemoryCaller | None = None,
        changed_fields: list[str] | None = None,
        superseded_by: str | None = None,
        correlation_id: str | None = None,
        memory_id: str | None = None,
    ) -> None:
        """Emit a memory lifecycle event if an event emitter is configured."""
        if self._event_emitter is None:
            return
        payload: dict[str, Any] = {
            "memory_id": memory_id or memory.memory_id,
            "category": memory.category.value,
            "scope": memory.scope.value,
            "trust_level": memory.trust_level.value,
            "source": memory.source.value,
        }
        if memory.scope_id:
            payload["scope_id"] = memory.scope_id
        if memory.source_id:
            payload["source_id"] = memory.source_id
        if changed_fields:
            payload["changed_fields"] = changed_fields
        if superseded_by:
            payload["superseded_by"] = superseded_by
        actor = caller.caller_id if caller else None
        if actor and len(actor) > 128:
            actor = actor[:128]
        await self._event_emitter.emit(
            event_type=event_type,
            source="memory",
            correlation_id=correlation_id or memory.memory_id,
            actor=actor,
            payload=payload,
        )

    @staticmethod
    def caller_from_request_context(ctx: Any) -> MemoryCaller:
        """
        Build a MemoryCaller from an authenticated RequestContext.

        The client must never be able to select caller_type or caller_id.
        """
        from app.identity.models import RequestContext, ActorType
        if not isinstance(ctx, RequestContext):
            raise ValueError("Expected RequestContext")
        if ctx.actor_type == ActorType.USER:
            if ctx.user_id is None:
                raise MemoryForbidden("User identity required for USER caller type")
            return MemoryCaller(CallerType.USER, ctx.user_id)
        elif ctx.actor_type == ActorType.SYSTEM:
            service_id = ctx.service_name or "unknown_system"
            return MemoryCaller(CallerType.SYSTEM, service_id)
        elif ctx.actor_type == ActorType.AGENT:
            if ctx.user_id is None:
                raise MemoryForbidden("User identity required for AGENT caller type")
            return MemoryCaller(CallerType.AGENT, ctx.user_id)
        else:
            raise ValueError(f"Unknown actor type: {ctx.actor_type}")

    # ── Authority checks ────────────────────────────────────────────────────────

    def _enforce_can_create(
        self,
        caller: MemoryCaller,
        category: MemoryCategory,
        trust_level: TrustLevel | None,
        source: ProvenanceSource | None,
    ) -> None:
        self._enforce_source_id(caller, category, source)

        if category not in _CREATOR_ALLOWED_CATEGORIES[caller.caller_type]:
            raise MemoryForbidden(
                f"Caller type '{caller.caller_type.value}' is not authorized to create "
                f"memories of category '{category.value}'"
            )

        resolved_trust = trust_level or _CATEGORY_DEFAULT_TRUST[category]
        if resolved_trust not in _CREATOR_ALLOWED_TRUST[caller.caller_type]:
            raise InvalidProvenance(
                f"Caller type '{caller.caller_type.value}' is not authorized to create "
                f"memories with trust level '{resolved_trust.value}'"
            )

        resolved_source = source or _CATEGORY_DEFAULT_SOURCE[category]
        if resolved_source not in _CREATOR_ALLOWED_SOURCE[caller.caller_type]:
            raise InvalidProvenance(
                f"Caller type '{caller.caller_type.value}' is not authorized to claim "
                f"provenance source '{resolved_source.value}'"
            )

        if category == MemoryCategory.USER_INSTRUCTION and not caller.is_user:
            raise InvalidProvenance(
                "Only a user caller can create 'user_instruction' memories"
            )

    def _enforce_can_update(self, caller: MemoryCaller, existing: MemoryEntry) -> None:
        self._enforce_source_id(caller, existing.source, existing.source_id)
        if caller.is_user:
            return
        if existing.source_id and existing.source_id != caller.caller_id:
            raise MemoryForbidden(
                f"Caller '{caller.caller_id}' is not the author of memory {existing.memory_id}"
            )
        if caller.is_system and existing.source != ProvenanceSource.SYSTEM:
            raise MemoryForbidden("System caller can only update its own memories")
        if caller.is_agent and existing.source != ProvenanceSource.AGENT:
            raise MemoryForbidden("Agent caller can only update its own memories")

    def _enforce_can_delete(self, caller: MemoryCaller, existing: MemoryEntry) -> None:
        self._enforce_source_id(caller, existing.source, existing.source_id)
        if caller.is_user:
            return
        if existing.source_id and existing.source_id != caller.caller_id:
            raise MemoryForbidden(
                f"Caller '{caller.caller_id}' is not the author of memory {existing.memory_id}"
            )
        if caller.is_system and existing.source != ProvenanceSource.SYSTEM:
            raise MemoryForbidden("System caller can only delete its own memories")
        if caller.is_agent and existing.source != ProvenanceSource.AGENT:
            raise MemoryForbidden("Agent caller can only delete its own memories")

    def _enforce_can_supersede(self, caller: MemoryCaller, existing: MemoryEntry) -> None:
        self._enforce_source_id(caller, existing.source, existing.source_id)
        if caller.is_user:
            return
        if existing.source_id and existing.source_id != caller.caller_id:
            raise MemoryForbidden(
                f"Caller '{caller.caller_id}' is not the author of memory {existing.memory_id}"
            )
        if caller.is_system and existing.source != ProvenanceSource.SYSTEM:
            raise MemoryForbidden("System caller can only supersede its own memories")
        if caller.is_agent and existing.source != ProvenanceSource.AGENT:
            raise MemoryForbidden("Agent caller can only supersede its own memories")

    def _enforce_source_id(
        self,
        caller: MemoryCaller,
        source: ProvenanceSource | None,
        source_id: str | None,
    ) -> None:
        if source in (ProvenanceSource.USER, ProvenanceSource.SYSTEM, ProvenanceSource.AGENT):
            if not source_id:
                raise InvalidProvenance(
                    f"source_id is required for memories with source '{source.value}'"
                )


__all__ = [
    "MemoryCaller",
    "MemoryService",
    "CallerType",
    "MemoryError",
    "MemoryNotFound",
    "MemoryForbidden",
    "InvalidProvenance",
]
