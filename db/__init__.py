"""Database package — SQLite persistence layer.

Exports:
    get_engine: Factory function trả về DatabaseEngine singleton.
    get_repos: Factory function trả về tuple repositories.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import DatabaseEngine
    from .repositories import (
        ComboRepository,
        JobRepository,
        SessionResultRepository,
    )


def get_engine(db_path: str | None = None) -> "DatabaseEngine":
    """Tạo và trả về DatabaseEngine instance.

    Args:
        db_path: Đường dẫn tới SQLite file. Default: runtime/data.db
    """
    from .engine import DatabaseEngine

    return DatabaseEngine(db_path=db_path or "runtime/data.db")


def get_repos(
    engine: "DatabaseEngine",
) -> tuple["ComboRepository", "JobRepository", "SessionResultRepository"]:
    """Tạo và trả về tuple (ComboRepository, JobRepository, SessionResultRepository).

    Args:
        engine: DatabaseEngine instance đã khởi tạo.
    """
    from .repositories import (
        ComboRepository,
        JobRepository,
        SessionResultRepository,
    )

    return (
        ComboRepository(engine),
        JobRepository(engine),
        SessionResultRepository(engine),
    )
