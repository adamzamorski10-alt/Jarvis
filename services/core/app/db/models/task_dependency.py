"""
ORM Model — Task Dependencies
DAG edges for DevelopmentTask records.
"""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TaskDependencyModel(Base):
    """
    SQLAlchemy ORM model for task dependency edges.

    Composite primary key: (task_id, depends_on_task_id)
    """

    __tablename__ = "task_dependencies"

    task_id: Mapped[str] = mapped_column(
        String(74),
        ForeignKey("development_tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        String(74),
        ForeignKey("development_tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    )

    def __repr__(self) -> str:
        return f"<TaskDependencyModel task_id={self.task_id!r} depends_on={self.depends_on_task_id!r}>"
