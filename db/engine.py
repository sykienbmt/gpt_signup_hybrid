"""DatabaseEngine — SQLite engine với WAL mode, transaction management, schema migration.

Quản lý single connection, WAL mode, BEGIN IMMEDIATE cho write transactions.
Schema migration tự động chạy khi khởi tạo engine.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Generator

from .schema import ALL_DDL, CURRENT_VERSION


# --- Exception hierarchy ---


class DatabaseError(Exception):
    """Base error cho database layer."""


class SchemaError(DatabaseError):
    """Schema migration failure."""


class DatabaseEngine:
    """SQLite engine với WAL mode và transaction management.

    Attributes:
        db_path: Path tới SQLite database file.
        is_closed: True nếu engine đã được close.
    """

    def __init__(self, db_path: Path | str = "runtime/data.db") -> None:
        """Khởi tạo engine. Tạo directories + file nếu chưa có.

        Args:
            db_path: Đường dẫn tới SQLite file.

        Raises:
            PermissionError: Nếu path không writable.
        """
        self._db_path = Path(db_path)
        self._closed = False

        # Tạo directories nếu thiếu
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Kiểm tra writable trước khi tạo connection
        self._check_writable()

        # Tạo connection và configure pragmas
        self._conn = self._create_connection()

        # Chạy schema migration
        self._migrate()

    def _check_writable(self) -> None:
        """Kiểm tra path có writable không.

        Raises:
            PermissionError: Nếu directory hoặc file không writable.
        """
        parent = self._db_path.parent

        # Nếu file đã tồn tại, kiểm tra file writable
        if self._db_path.exists():
            if not os.access(self._db_path, os.W_OK):
                raise PermissionError(
                    f"Database file is not writable: {self._db_path}"
                )
        else:
            # File chưa tồn tại — kiểm tra directory writable
            if not os.access(parent, os.W_OK):
                raise PermissionError(
                    f"Directory is not writable, cannot create database: {parent}"
                )

    def _create_connection(self) -> sqlite3.Connection:
        """Tạo và configure SQLite connection."""
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # Manual transaction control
        )
        conn.row_factory = sqlite3.Row

        # Configure pragmas
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")

        return conn

    def _migrate(self) -> None:
        """Chạy schema migration nếu cần.

        Đọc version từ `_schema_version` table, so sánh với CURRENT_VERSION.
        Nếu chưa có table hoặc version cũ hơn → execute toàn bộ DDL trong 1 transaction.

        Raises:
            SchemaError: Nếu migration fail (DDL error).
        """
        current_db_version = self._get_schema_version()

        if current_db_version >= CURRENT_VERSION:
            return

        # Execute ALL DDL trong single transaction
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            for ddl_block in ALL_DDL:
                # Mỗi DDL block có thể chứa nhiều statements (ngăn cách bởi ;)
                for statement in self._split_statements(ddl_block):
                    self._conn.execute(statement)
            # Ghi version mới
            self._conn.execute(
                "INSERT OR REPLACE INTO _schema_version (version, description) VALUES (?, ?)",
                (CURRENT_VERSION, f"Migration to version {CURRENT_VERSION}"),
            )
            self._conn.execute("COMMIT")
        except Exception as exc:
            self._conn.execute("ROLLBACK")
            raise SchemaError(
                f"Schema migration to version {CURRENT_VERSION} failed: {exc}"
            ) from exc

    @staticmethod
    def _split_statements(ddl_block: str) -> list[str]:
        """Split DDL block thành individual SQL statements.

        Loại bỏ empty strings và whitespace-only.
        """
        statements = []
        for stmt in ddl_block.split(";"):
            stripped = stmt.strip()
            if stripped:
                statements.append(stripped + ";")
        return statements

    def _get_schema_version(self) -> int:
        """Đọc schema version hiện tại từ database.

        Returns:
            Version number, hoặc 0 nếu table chưa tồn tại.
        """
        try:
            row = self._conn.execute(
                "SELECT MAX(version) FROM _schema_version"
            ).fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            # Table _schema_version chưa tồn tại
            return 0

    @contextlib.contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager cho write transactions.

        Sử dụng BEGIN IMMEDIATE để acquire lock sớm, tránh deadlock.
        Auto-commit on success, rollback on exception, re-raise original exception.

        Yields:
            sqlite3.Connection đã bắt đầu transaction.

        Raises:
            Bất kỳ exception nào xảy ra trong block — được re-raise sau rollback.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    @contextlib.asynccontextmanager
    async def get_connection_async(self):
        """Async wrapper cho get_connection() qua asyncio.to_thread.

        Yields:
            sqlite3.Connection đã bắt đầu transaction (chạy trong thread).
        """
        # Chạy toàn bộ transaction logic trong thread riêng không khả thi
        # vì context manager cần yield connection cho caller.
        # Thay vào đó, wrap BEGIN/COMMIT/ROLLBACK qua to_thread.
        await asyncio.to_thread(self._conn.execute, "BEGIN IMMEDIATE")
        try:
            yield self._conn
            await asyncio.to_thread(self._conn.execute, "COMMIT")
        except BaseException:
            await asyncio.to_thread(self._conn.execute, "ROLLBACK")
            raise

    def raw_connection(self) -> sqlite3.Connection:
        """Trả về connection cho read-only operations.

        Không bắt đầu transaction (không BEGIN IMMEDIATE).
        Caller KHÔNG nên dùng để write — dùng get_connection() cho writes.

        Returns:
            sqlite3.Connection hiện tại.
        """
        return self._conn

    @property
    def is_closed(self) -> bool:
        """True nếu engine đã được close."""
        return self._closed

    @property
    def db_path(self) -> Path:
        """Path tới database file."""
        return self._db_path
