"""
Provider-Neutral Development Agent Abstraction.

Defines the contract for future coding agents (Stage 9 Kilo, etc.).
"""
from abc import ABC, abstractmethod

from app.orchestration.models import DevelopmentAgentContext, DevelopmentResult


class DevelopmentAgent(ABC):
    """
    Abstract interface for autonomous coding agents.

    Implementations execute inside an isolated worktree path provided in context
    and return a structured DevelopmentResult.
    """

    @abstractmethod
    async def execute(self, context: DevelopmentAgentContext) -> DevelopmentResult:
        """
        Execute the development task inside the isolated workspace context.

        Args:
            context: Immutable development agent context containing workspace path,
                     task description, and repository details.

        Returns:
            DevelopmentResult indicating SUCCESS, FAILURE, ERROR, CANCELLED, or TIMEOUT.
        """
        ...
