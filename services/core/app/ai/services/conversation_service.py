"""Conversation persistence for text-only AI assistant."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.conversation import Conversation, Message


class ConversationNotFound(ValueError):
    pass


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, provider: str, model: str | None, title: str | None = None) -> Conversation:
        conversation = Conversation(
            id=uuid4().hex,
            title=title or "New conversation",
            provider=provider,
            model=model,
        )
        self._session.add(conversation)
        await self._session.commit()
        return conversation

    async def list(self) -> list[Conversation]:
        result = await self._session.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, conversation_id: str) -> Conversation:
        result = await self._session.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages))
            .execution_options(populate_existing=True)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise ConversationNotFound("Conversation not found")
        return conversation

    async def add_message(
        self,
        conversation: Conversation,
        role: str,
        content: str,
        client_message_id: str | None = None,
    ) -> Message:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("Invalid message role")

        # Deduplicate user messages based on unique client_message_id
        if role == "user" and client_message_id:
            result = await self._session.execute(
                select(Message).where(
                    Message.conversation_id == conversation.id,
                    Message.role == "user",
                    Message.client_message_id == client_message_id,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                return existing

        message = Message(
            conversation_id=conversation.id,
            role=role,
            content=content,
            client_message_id=client_message_id,
        )
        conversation.updated_at = datetime.now(timezone.utc)
        if role == "user" and conversation.title == "New conversation":
            conversation.title = content.strip().replace("\n", " ")[:80] or "New conversation"
        self._session.add(message)
        await self._session.commit()
        return message

    def build_context_messages(
        self,
        conversation: Conversation,
        max_messages: int = 24,
        max_chars: int = 24000,
    ) -> list[dict[str, str]]:
        """Return a bounded, provider-neutral context window for chat calls."""
        selected = list(getattr(conversation, "messages", []))[-max_messages:]
        output: list[dict[str, str]] = []
        remaining = max_chars
        for message in reversed(selected):
            content = message.content or ""
            if len(content) > remaining:
                content = content[-remaining:]
            if content:
                output.append({"role": message.role, "content": content})
                remaining -= len(content)
            if remaining <= 0:
                break
        return list(reversed(output))
