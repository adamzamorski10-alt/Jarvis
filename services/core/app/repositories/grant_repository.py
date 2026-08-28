"""
Repository — Permission Grants
Data access layer for user-granted tool execution permissions in SQLite.
"""
from datetime import datetime, timezone
import uuid
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.permission_grant import PermissionGrantModel
from app.tools.models import GrantType, PermissionGrant


def match_action(pattern: str, action: str) -> bool:
    """
    Safely checks if an action matches an action pattern.
    - "*" matches any action.
    - Exact match (e.g. "read" == "read").
    - Case-insensitive exact match.
    """
    pattern_clean = pattern.strip().lower()
    action_clean = action.strip().lower()

    if pattern_clean == "*":
        return True
    return pattern_clean == action_clean


def match_tool_id(grant_tool_id: str, requested_tool_id: str) -> bool:
    """
    Safely checks if requested_tool_id matches grant_tool_id.
    - Exact match (e.g. "files.read_file" == "files.read_file").
    - Domain wildcard (e.g. "files.*" matches "files.read_file", "files.write_file", but NOT "system.restart").
    - Global wildcard "*" matches any tool.
    """
    g_clean = grant_tool_id.strip().lower()
    req_clean = requested_tool_id.strip().lower()

    if g_clean == "*" or g_clean == req_clean:
        return True

    if g_clean.endswith(".*"):
        prefix = g_clean[:-2]
        return req_clean.startswith(prefix + ".")

    return False


class GrantRepository:
    """
    Data access and atomic operation repository for PermissionGrant records.
    """
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_grant(
        self,
        tool_id: str,
        grant_type: GrantType | str,
        action_pattern: str = "*",
        expires_at: datetime | None = None,
        grant_id: str | None = None,
    ) -> PermissionGrant:
        g_type_str = grant_type.value if isinstance(grant_type, GrantType) else str(grant_type)
        gid = grant_id or f"grant_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        grant_model = PermissionGrantModel(
            id=gid,
            tool_id=tool_id.strip(),
            action_pattern=action_pattern.strip(),
            grant_type=g_type_str,
            created_at=now,
            expires_at=expires_at,
            consumed=False,
        )

        self._session.add(grant_model)
        await self._session.flush()
        await self._session.refresh(grant_model)

        return self._to_domain(grant_model)

    async def get_grant(self, grant_id: str) -> PermissionGrant | None:
        result = await self._session.execute(
            select(PermissionGrantModel).where(PermissionGrantModel.id == grant_id)
        )
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def find_matching_grant(
        self,
        tool_id: str,
        action: str,
        at: datetime | None = None,
    ) -> PermissionGrant | None:
        """
        Finds the first valid active grant that matches tool_id and action.
        - Ignores consumed one_time grants.
        - Ignores expired time_bound grants.
        """
        now = at or datetime.now(timezone.utc)

        result = await self._session.execute(
            select(PermissionGrantModel)
            .where(PermissionGrantModel.consumed == False)
            .order_by(PermissionGrantModel.created_at.desc())
        )
        models = list(result.scalars().all())

        for model in models:
            # Check expiration if time-bound
            if model.grant_type == GrantType.TIME_BOUND.value:
                expires = model.expires_at
                if expires is None:
                    continue
                # SQLite may return timezone-naive datetimes; normalize to UTC-aware
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= now:
                    continue

            # Check tool_id matching
            if not match_tool_id(model.tool_id, tool_id):
                continue

            # Check action pattern matching
            if not match_action(model.action_pattern, action):
                continue

            return self._to_domain(model)

        return None

    async def consume_one_time_grant_atomically(self, grant_id: str) -> bool:
        """
        Atomically consumes a one_time grant in a single atomic SQL UPDATE statement.
        Guarantees that parallel requests cannot consume the same one_time grant.
        Returns True if the grant was successfully consumed by this call, False otherwise.
        """
        stmt = (
            update(PermissionGrantModel)
            .where(
                PermissionGrantModel.id == grant_id,
                PermissionGrantModel.grant_type == GrantType.ONE_TIME.value,
                PermissionGrantModel.consumed == False,
            )
            .values(consumed=True)
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        return res.rowcount == 1

    async def revoke_grant(self, grant_id: str) -> bool:
        """
        Revokes (deletes) a permission grant by ID.
        Returns True if grant existed and was deleted.
        """
        stmt = delete(PermissionGrantModel).where(PermissionGrantModel.id == grant_id)
        res = await self._session.execute(stmt)
        await self._session.flush()
        return res.rowcount > 0

    async def cleanup_expired_grants(self, at: datetime | None = None) -> int:
        """
        Deletes all expired time_bound grants.
        Returns the count of deleted grants.
        """
        now = at or datetime.now(timezone.utc)
        stmt = delete(PermissionGrantModel).where(
            PermissionGrantModel.grant_type == GrantType.TIME_BOUND.value,
            PermissionGrantModel.expires_at <= now,
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        return res.rowcount

    async def list_grants(
        self,
        tool_id: str | None = None,
        grant_type: GrantType | str | None = None,
        include_consumed: bool = False,
    ) -> list[PermissionGrant]:
        """
        Returns a list of grants matching optional filters.
        """
        query = select(PermissionGrantModel)
        if not include_consumed:
            query = query.where(PermissionGrantModel.consumed == False)
        if tool_id:
            query = query.where(PermissionGrantModel.tool_id == tool_id)
        if grant_type:
            g_str = grant_type.value if isinstance(grant_type, GrantType) else str(grant_type)
            query = query.where(PermissionGrantModel.grant_type == g_str)

        query = query.order_by(PermissionGrantModel.created_at.desc())
        result = await self._session.execute(query)
        models = list(result.scalars().all())
        return [self._to_domain(m) for m in models]

    @staticmethod
    def _to_domain(model: PermissionGrantModel | None) -> PermissionGrant | None:
        if model is None:
            return None
        # Use model_construct to bypass Pydantic validation when reconstructing from DB.
        # DB records are trusted; already-expired time_bound grants must be readable.
        return PermissionGrant.model_construct(
            id=model.id,
            tool_id=model.tool_id,
            action_pattern=model.action_pattern,
            grant_type=GrantType(model.grant_type),
            created_at=model.created_at,
            expires_at=model.expires_at,
        )
