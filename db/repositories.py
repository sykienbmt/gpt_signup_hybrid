"""Repository layer — Data access abstraction cho SQLite persistence.

Cung cấp ComboRepository, JobRepository, SessionResultRepository.
Business logic modules inject repository qua constructor, không dùng raw SQL trực tiếp.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .engine import DatabaseError

if TYPE_CHECKING:
    from .engine import DatabaseEngine


# --- Exception classes ---


class RepositoryError(DatabaseError):
    """Repository operation failure.

    Attributes:
        operation: Tên method đã fail (e.g., "mark_success").
        cause: Original exception.
    """

    def __init__(self, operation: str, cause: Exception) -> None:
        self.operation = operation
        self.cause = cause
        super().__init__(f"{operation} failed: {cause}")


# --- Terminal error substrings cho pick_available filtering ---

TERMINAL_ERROR_SUBSTRINGS: list[str] = [
    "registration_disallowed",
    "invalid_grant",
    "AADSTS50173",
    "AADSTS70008",
]


# --- ComboRepository ---


class ComboRepository:
    """Data access cho `outlook_combos` table.

    Cung cấp CRUD operations + business logic queries (pick_available, mark_success/failure).
    """

    def __init__(self, engine: "DatabaseEngine") -> None:
        self._engine = engine

    def get_by_email(self, email: str) -> dict | None:
        """Lấy combo theo email.

        Returns:
            dict chứa tất cả columns, hoặc None nếu không tìm thấy.
        """
        conn = self._engine.raw_connection()
        row = conn.execute(
            "SELECT * FROM outlook_combos WHERE email = ?", (email,)
        ).fetchone()
        return dict(row) if row else None

    def upsert(self, combo_data: dict) -> None:
        """Insert hoặc update combo.

        Nếu email đã tồn tại: preserve used_for_signup, used_at, last_error, last_failed_at.
        Overwrite: password, refresh_token, client_id.

        Args:
            combo_data: dict với keys: email, password, refresh_token, client_id.

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO outlook_combos (email, password, refresh_token, client_id)
                    VALUES (:email, :password, :refresh_token, :client_id)
                    ON CONFLICT(email) DO UPDATE SET
                        password = excluded.password,
                        refresh_token = excluded.refresh_token,
                        client_id = excluded.client_id
                    """,
                    combo_data,
                )
        except Exception as exc:
            raise RepositoryError("upsert", exc) from exc

    def mark_success(self, email: str) -> None:
        """Đánh dấu combo đã signup thành công.

        Sets: used_for_signup=1, used_at=now(UTC), last_error=NULL.

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE outlook_combos
                    SET used_for_signup = 1,
                        used_at = ?,
                        last_error = NULL
                    WHERE email = ?
                    """,
                    (datetime.now(timezone.utc).isoformat(), email),
                )
        except Exception as exc:
            raise RepositoryError("mark_success", exc) from exc

    def mark_failure(self, email: str, error: str) -> None:
        """Đánh dấu combo bị lỗi signup.

        Sets: last_error=error, last_failed_at=now(UTC).
        KHÔNG thay đổi used_for_signup.

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE outlook_combos
                    SET last_error = ?,
                        last_failed_at = ?
                    WHERE email = ?
                    """,
                    (error, datetime.now(timezone.utc).isoformat(), email),
                )
        except Exception as exc:
            raise RepositoryError("mark_failure", exc) from exc

    def pick_available(self) -> dict | None:
        """Chọn combo khả dụng cho signup.

        Filter:
            - used_for_signup = 0
            - last_error IS NULL hoặc không chứa terminal error substrings
        Order: created_at ASC (lấy combo cũ nhất trước).

        Returns:
            dict chứa combo data, hoặc None nếu pool exhausted.
        """
        conn = self._engine.raw_connection()
        row = conn.execute(
            """
            SELECT * FROM outlook_combos
            WHERE used_for_signup = 0
              AND (
                last_error IS NULL
                OR (
                    last_error NOT LIKE '%registration_disallowed%'
                    AND last_error NOT LIKE '%invalid_grant%'
                    AND last_error NOT LIKE '%AADSTS50173%'
                    AND last_error NOT LIKE '%AADSTS70008%'
                )
              )
            ORDER BY created_at ASC
            LIMIT 1
            """,
        ).fetchone()
        return dict(row) if row else None

    def update_refresh_token(self, email: str, token: str) -> None:
        """Cập nhật refresh token sau rotation.

        Sets: refresh_token=token, last_refresh_at=now(UTC).

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE outlook_combos
                    SET refresh_token = ?,
                        last_refresh_at = ?
                    WHERE email = ?
                    """,
                    (token, datetime.now(timezone.utc).isoformat(), email),
                )
        except Exception as exc:
            raise RepositoryError("update_refresh_token", exc) from exc

    def list_all(self) -> list[dict]:
        """Trả về tất cả combos.

        Returns:
            List of dicts, mỗi dict là 1 row từ outlook_combos.
        """
        conn = self._engine.raw_connection()
        rows = conn.execute("SELECT * FROM outlook_combos").fetchall()
        return [dict(row) for row in rows]


# --- SessionResultRepository ---


class SessionResultRepository:
    """Data access cho `session_results` table.

    Cung cấp CRUD operations + serialization/deserialization cho JSON fields (cookies, two_factor).
    """

    def __init__(self, engine: "DatabaseEngine") -> None:
        self._engine = engine

    def create(self, result_data: dict) -> int:
        """Insert session result mới.

        Serialize `cookies` (list→JSON string) và `two_factor` (dict→JSON string) nếu có.

        Args:
            result_data: dict với keys tương ứng columns trong session_results table.

        Returns:
            Auto-increment id của row vừa insert.

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                # Serialize JSON fields
                cookies_raw = result_data.get("cookies")
                cookies_json = json.dumps(cookies_raw) if cookies_raw is not None else None

                two_factor_raw = result_data.get("two_factor")
                two_factor_json = json.dumps(two_factor_raw) if two_factor_raw is not None else None

                cursor = conn.execute(
                    """
                    INSERT INTO session_results
                        (email, password, name, age, user_id, account_id,
                         session_token, access_token, cookies, two_factor,
                         phase1_seconds, phase2_seconds, otp_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result_data.get("email"),
                        result_data.get("password"),
                        result_data.get("name"),
                        result_data.get("age"),
                        result_data.get("user_id"),
                        result_data.get("account_id"),
                        result_data.get("session_token"),
                        result_data.get("access_token"),
                        cookies_json,
                        two_factor_json,
                        result_data.get("phase1_seconds"),
                        result_data.get("phase2_seconds"),
                        result_data.get("otp_seconds"),
                    ),
                )
                return cursor.lastrowid
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError("create", exc) from exc

    def get_by_email(self, email: str) -> dict | None:
        """Lấy session result mới nhất theo email.

        Returns:
            dict chứa tất cả columns (raw, chưa deserialize JSON), hoặc None nếu không tìm thấy.
        """
        conn = self._engine.raw_connection()
        row = conn.execute(
            """
            SELECT * FROM session_results
            WHERE email = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        return dict(row) if row else None

    def update_2fa(self, email: str, mfa_data: dict) -> None:
        """Cập nhật two_factor cho session result mới nhất của email.

        Tìm row mới nhất (ORDER BY created_at DESC LIMIT 1), UPDATE two_factor column.

        Args:
            email: Email cần update.
            mfa_data: Dict chứa 2FA data, sẽ được serialize sang JSON.

        Raises:
            RepositoryError: Nếu không tìm thấy row cho email, hoặc write fail.
        """
        try:
            with self._engine.get_connection() as conn:
                # Tìm id của row mới nhất
                row = conn.execute(
                    """
                    SELECT id FROM session_results
                    WHERE email = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (email,),
                ).fetchone()

                if row is None:
                    raise RepositoryError(
                        "update_2fa",
                        ValueError(f"No session result found for email: {email}"),
                    )

                conn.execute(
                    "UPDATE session_results SET two_factor = ? WHERE id = ?",
                    (json.dumps(mfa_data), row["id"]),
                )
        except RepositoryError:
            raise
        except Exception as exc:
            raise RepositoryError("update_2fa", exc) from exc

    def export_json(self, email: str) -> dict | None:
        """Export session result mới nhất cho email, deserialize JSON fields.

        Deserialize `cookies` (JSON→list) và `two_factor` (JSON→dict).

        Returns:
            dict với cookies là list và two_factor là dict, hoặc None nếu không tìm thấy.
        """
        conn = self._engine.raw_connection()
        row = conn.execute(
            """
            SELECT * FROM session_results
            WHERE email = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email,),
        ).fetchone()

        if row is None:
            return None

        result = dict(row)

        # Deserialize cookies: JSON string → list
        if result.get("cookies") is not None:
            result["cookies"] = json.loads(result["cookies"])

        # Deserialize two_factor: JSON string → dict
        if result.get("two_factor") is not None:
            result["two_factor"] = json.loads(result["two_factor"])

        return result

    def list_all(self) -> list[dict]:
        """Trả về tất cả session results, ordered by created_at DESC.

        Returns:
            List of dicts, mỗi dict là 1 row (raw, chưa deserialize JSON fields).
        """
        conn = self._engine.raw_connection()
        rows = conn.execute(
            "SELECT * FROM session_results ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


# --- Terminal statuses for job lifecycle ---

_TERMINAL_STATUSES = ("success", "error", "cancelled")


# --- JobRepository ---


class JobRepository:
    """Data access cho `jobs` và `job_logs` tables.

    Quản lý job lifecycle: create, status transitions, log append,
    recovery sau restart, và cleanup finished jobs.
    """

    def __init__(self, engine: "DatabaseEngine") -> None:
        self._engine = engine

    def create(self, job_data: dict) -> str:
        """Tạo job mới.

        Args:
            job_data: dict chứa keys: id, email, combo, mail_mode, status,
                      created_at, job_type. Các fields khác optional.

        Returns:
            job_id (string) của job vừa tạo.

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        id, email, combo, mail_mode, status, error, password,
                        secret, first_code, user_id, session_path, payment_link,
                        created_at, started_at, finished_at, job_type
                    ) VALUES (
                        :id, :email, :combo, :mail_mode, :status, :error, :password,
                        :secret, :first_code, :user_id, :session_path, :payment_link,
                        :created_at, :started_at, :finished_at, :job_type
                    )
                    """,
                    {
                        "id": job_data["id"],
                        "email": job_data["email"],
                        "combo": job_data["combo"],
                        "mail_mode": job_data.get("mail_mode", "outlook"),
                        "status": job_data.get("status", "queued"),
                        "error": job_data.get("error"),
                        "password": job_data.get("password"),
                        "secret": job_data.get("secret"),
                        "first_code": job_data.get("first_code"),
                        "user_id": job_data.get("user_id"),
                        "session_path": job_data.get("session_path"),
                        "payment_link": job_data.get("payment_link"),
                        "created_at": job_data["created_at"],
                        "started_at": job_data.get("started_at"),
                        "finished_at": job_data.get("finished_at"),
                        "job_type": job_data.get("job_type", "signup"),
                    },
                )
        except Exception as exc:
            raise RepositoryError("create", exc) from exc
        return job_data["id"]

    def update_status(self, job_id: str, status: str, **kwargs: object) -> None:
        """Cập nhật status của job.

        Nếu status == "running": set started_at = time.time().
        Nếu status in ("success", "error", "cancelled"): set finished_at = time.time().
        Accepts extra kwargs cho các fields khác (error, password, secret, etc.).

        Args:
            job_id: ID của job.
            status: Status mới.
            **kwargs: Extra fields để update (error, password, secret, first_code,
                      user_id, session_path, payment_link).

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        set_clauses = ["status = ?"]
        params: list[object] = [status]

        if status == "running":
            set_clauses.append("started_at = ?")
            params.append(time.time())
        elif status in _TERMINAL_STATUSES:
            set_clauses.append("finished_at = ?")
            params.append(time.time())

        # Extra kwargs — chỉ update các columns hợp lệ
        _allowed_extra = (
            "error", "password", "secret", "first_code",
            "user_id", "session_path", "payment_link",
        )
        for key, value in kwargs.items():
            if key in _allowed_extra:
                set_clauses.append(f"{key} = ?")
                params.append(value)

        params.append(job_id)

        try:
            with self._engine.get_connection() as conn:
                conn.execute(
                    f"UPDATE jobs SET {', '.join(set_clauses)} WHERE id = ?",
                    params,
                )
        except Exception as exc:
            raise RepositoryError("update_status", exc) from exc

    def append_log(self, job_id: str, line: str) -> None:
        """Thêm log line cho job.

        Args:
            job_id: ID của job.
            line: Nội dung log line.

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                conn.execute(
                    "INSERT INTO job_logs (job_id, line) VALUES (?, ?)",
                    (job_id, line),
                )
        except Exception as exc:
            raise RepositoryError("append_log", exc) from exc

    def get_by_id(self, job_id: str) -> dict | None:
        """Lấy job theo ID.

        Returns:
            dict chứa tất cả columns của job, hoặc None nếu không tìm thấy.
        """
        conn = self._engine.raw_connection()
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        """Trả về tất cả jobs, ordered by created_at ASC.

        Returns:
            List of dicts.
        """
        conn = self._engine.raw_connection()
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_by_status(self, status: str) -> list[dict]:
        """Trả về jobs theo status, ordered by created_at ASC.

        Args:
            status: Status để filter.

        Returns:
            List of dicts.
        """
        conn = self._engine.raw_connection()
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC",
            (status,),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, job_id: str) -> None:
        """Xoá job theo ID (cascade xoá job_logs).

        Args:
            job_id: ID của job cần xoá.

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        except Exception as exc:
            raise RepositoryError("delete", exc) from exc

    def delete_finished(self) -> int:
        """Xoá tất cả jobs đã hoàn thành (status = 'success' hoặc 'error').

        Cascade xoá job_logs liên quan.

        Returns:
            Số lượng jobs đã xoá.

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM jobs WHERE status IN ('success', 'error')"
                )
                return cursor.rowcount
        except Exception as exc:
            raise RepositoryError("delete_finished", exc) from exc

    def get_logs(self, job_id: str) -> list[dict]:
        """Lấy tất cả log lines của job, ordered by created_at ASC.

        Args:
            job_id: ID của job.

        Returns:
            List of dicts với keys: id, job_id, line, created_at.
        """
        conn = self._engine.raw_connection()
        rows = conn.execute(
            "SELECT * FROM job_logs WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def recover_interrupted(self) -> list[dict]:
        """Recover jobs bị interrupted (queued hoặc running).

        - SELECT jobs WHERE status IN ('queued', 'running')
        - Reset running → queued, clear started_at
        - Return tất cả ordered by created_at ASC

        Returns:
            List of dicts (jobs đã được recover, status = 'queued').

        Raises:
            RepositoryError: Nếu write operation fail.
        """
        try:
            with self._engine.get_connection() as conn:
                # Reset running → queued, clear started_at
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', started_at = NULL
                    WHERE status = 'running'
                    """
                )
        except Exception as exc:
            raise RepositoryError("recover_interrupted", exc) from exc

        # Read all queued jobs (bao gồm cả jobs vừa được reset)
        conn = self._engine.raw_connection()
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'running')
            ORDER BY created_at ASC
            """,
        ).fetchall()
        return [dict(row) for row in rows]
