"""Mail providers cho OTP polling.

2 backends:
    - WorkerMailProvider: Cloudflare Worker logs API (icloud-cf-mail style).
    - OutlookMailProvider: Microsoft Graph API qua refresh_token (combo Outlook).

Mỗi provider có method:
    async def poll_otp(*, recipient, started_at, timeout_seconds, poll_interval_seconds, log) -> str
"""
from __future__ import annotations

import asyncio
import json
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx


_OTP_REGEX = re.compile(
    r"(?:verification\s+code|one[-\s]*time\s+(?:password|code)|security\s+code|login\s+code)"
    r"[^0-9]{0,40}(\d{6})"
    r"|(?<!\d)(\d{6})(?!\d)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_dt(value: Any) -> datetime | None:
    """Parse datetime từ nhiều format khác nhau."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S GMT", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _extract_otp(subject: str, body: str) -> str | None:
    """Tìm code 6 chữ số trong subject + body."""
    cleaned = re.sub(r"<[^>]*>", " ", f"{subject}\n{body}")
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    match = _OTP_REGEX.search(cleaned)
    if not match:
        return None
    return match.group(1) or match.group(2)


def _is_openai_sender(sender: str) -> bool:
    """Filter mail từ OpenAI để tránh nhặt nhầm OTP của dịch vụ khác."""
    s = (sender or "").lower()
    return any(d in s for d in ("openai.com", "auth.openai.com", "noreply@openai", "tm.openai.com"))


class MailProvider(Protocol):
    """Interface chung."""

    async def poll_otp(
        self,
        *,
        recipient: str,
        started_at: datetime,
        timeout_seconds: float,
        poll_interval_seconds: float,
        log,
    ) -> str:
        ...


# ─────────────────────────────────────────────────────────────────────
# Worker provider (icloud-cf-mail style)
# ─────────────────────────────────────────────────────────────────────


class WorkerMailProvider:
    """Cloudflare Worker logs API.

    Worker trả JSON:
        - list trực tiếp [{to, subject, body, date, ...}, ...]
        - hoặc dict {messages|items|logs|emails|data: [...]}
    """

    def __init__(self, *, logs_url: str, api_key: str | None, insecure_tls: bool = True):
        if not logs_url:
            raise ValueError("Worker logs_url is required")
        self.logs_url = logs_url
        self.api_key = api_key
        self.insecure_tls = insecure_tls

    @staticmethod
    def _normalize(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("messages", "items", "logs", "emails", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return []

    async def poll_otp(
        self,
        *,
        recipient: str,
        started_at: datetime,
        timeout_seconds: float,
        poll_interval_seconds: float,
        log,
    ) -> str:
        mailbox = recipient.strip().lower()
        if not mailbox:
            raise ValueError("recipient is required")

        headers: dict[str, str] = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if self.insecure_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            verify: Any = ctx
        else:
            verify = True

        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        log(f"[otp:worker] polling {mailbox} (timeout {timeout_seconds:.0f}s)")

        async with httpx.AsyncClient(verify=verify, timeout=20.0, follow_redirects=True) as client:
            attempt = 0
            while True:
                attempt += 1
                try:
                    response = await client.get(
                        f"{self.logs_url}?mail={quote(mailbox)}",
                        headers=headers,
                    )
                    if response.status_code != 200:
                        log(f"[otp:worker] HTTP {response.status_code} attempt {attempt}")
                    else:
                        messages = self._normalize(response.json())
                        messages.sort(
                            key=lambda m: (
                                _parse_dt(m.get("date") or m.get("receivedAt") or m.get("created_at"))
                                or datetime.min.replace(tzinfo=timezone.utc)
                            ),
                            reverse=True,
                        )
                        for msg in messages:
                            msg_to = str(msg.get("to") or "").strip().lower()
                            if msg_to and msg_to != mailbox:
                                continue
                            msg_dt = _parse_dt(msg.get("date") or msg.get("receivedAt") or msg.get("created_at"))
                            if msg_dt is not None and msg_dt < started_at:
                                continue
                            subject = str(msg.get("subject") or "")
                            body = (
                                msg.get("bodyText") or msg.get("text") or msg.get("body")
                                or msg.get("htmlBody") or msg.get("content") or msg.get("html") or ""
                            )
                            code = _extract_otp(subject, str(body))
                            if code:
                                log(f"[otp:worker] found {code} (attempt {attempt})")
                                return code
                except (httpx.HTTPError, ValueError) as exc:
                    log(f"[otp:worker] error attempt {attempt}: {exc}")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"OTP timeout after {timeout_seconds}s for {mailbox}")
                await asyncio.sleep(min(poll_interval_seconds, remaining))


# ─────────────────────────────────────────────────────────────────────
# Outlook provider (Microsoft Graph)
# ─────────────────────────────────────────────────────────────────────


_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
_DEFAULT_SCOPE = "https://graph.microsoft.com/.default offline_access"

# Folder names dùng tìm OTP — Inbox + Junk vì OpenAI mail thi thoảng vào spam.
_OTP_FOLDERS = ("Inbox", "Junk Email")

# Microsoft refresh / Graph: timeout tổng 12s, connect 6s — đủ để fail nhanh + retry.
_OUTLOOK_HTTP_TIMEOUT = httpx.Timeout(connect=6.0, read=12.0, write=12.0, pool=6.0)

# Sau N lần network/HTTP transient liên tiếp → coi combo này transient-dead trong run hiện tại.
# Raise terminal error để job kết thúc nhanh thay vì chờ OTP timeout (180s).
_OUTLOOK_CONNECT_FAIL_THRESHOLD = 3

# Auth-fail strings → combo dead vĩnh viễn (revoke / disabled / format invalid)
_OUTLOOK_AUTH_FATAL_KEYS = (
    "invalid_grant",
    "AADSTS50173",  # FreshTokenNeeded — refresh token revoked
    "AADSTS70008",  # Refresh token expired
    "AADSTS50034",  # User account does not exist
    "AADSTS50057",  # User account is disabled
    "AADSTS700016",  # Application not found
    "unauthorized_client",
)


class OutlookComboError(Exception):
    """Combo Outlook parse/refresh fail (terminal — combo coi như dead)."""


class OutlookProviderUnavailable(Exception):
    """Outlook provider tạm thời không thể hoạt động (network/proxy fail).

    Khác với OutlookComboError ở chỗ: combo có thể vẫn sống, chỉ là network
    đến Microsoft đang fail. Caller có thể retry sau hoặc rotate proxy.
    """


class OutlookCombo:
    """Combo format: `email|password|refresh_token|client_id`.

    Component:
        email          — bpkknbrl2278@hotmail.com
        password       — không dùng cho refresh flow, lưu để re-login fallback
        refresh_token  — M.C535_BAY... (rotate sau mỗi refresh)
        client_id      — 8b4ba9dd-3ea5-4e5f-86f1-ddba2230dcf2 (Outlook desktop pre-auth)
    """

    __slots__ = ("email", "password", "refresh_token", "client_id")

    def __init__(self, email: str, password: str, refresh_token: str, client_id: str):
        self.email = email
        self.password = password
        self.refresh_token = refresh_token
        self.client_id = client_id

    @classmethod
    def parse(cls, combo: str) -> "OutlookCombo":
        parts = combo.split("|")
        if len(parts) != 4:
            raise OutlookComboError(
                f"combo phải có 4 phần (email|password|refresh_token|client_id), nhận {len(parts)}"
            )
        email, password, refresh_token, client_id = (p.strip() for p in parts)
        if not email or "@" not in email:
            raise OutlookComboError(f"email không hợp lệ: {email!r}")
        if not refresh_token.startswith("M.C"):
            raise OutlookComboError("refresh_token không bắt đầu bằng 'M.C' (sai format)")
        if len(client_id) != 36 or client_id.count("-") != 4:
            raise OutlookComboError(f"client_id không phải UUID: {client_id!r}")
        return cls(email=email, password=password, refresh_token=refresh_token, client_id=client_id)


class OutlookMailProvider:
    """Microsoft Graph mail provider.

    - Tự động refresh token khi access expire.
    - Persist rotate refresh_token ra disk (`runtime/outlook_state/<email>.json`).
      Nếu không persist, lần sau dùng refresh_token cũ sẽ bị `invalid_grant`.
    """

    def __init__(
        self,
        *,
        combo: OutlookCombo,
        state_dir: Path,
        scope: str = _DEFAULT_SCOPE,
        proxy: str | None = None,
    ):
        self.combo = combo
        self.scope = scope
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = state_dir / f"{combo.email.replace('/', '_')}.json"
        self.proxy = proxy.strip() if isinstance(proxy, str) and proxy.strip() else None
        self._access_token: str | None = None
        self._access_expires_at: float = 0.0
        # Hydrate state nếu đã từng refresh
        self._hydrate_state()

    def _hydrate_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        latest = data.get("refresh_token")
        if isinstance(latest, str) and latest.startswith("M.C"):
            self.combo.refresh_token = latest

    def _persist_state(self, token_data: dict[str, Any]) -> None:
        # Merge với state hiện tại để giữ các field không liên quan đến token
        # (registered_password, last_error, used_for_signup, v.v.)
        try:
            existing = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            existing = {}
        existing.update({
            "email": self.combo.email,
            "client_id": self.combo.client_id,
            "refresh_token": self.combo.refresh_token,
            "last_refresh_at": datetime.now(timezone.utc).isoformat(),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
        })
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def _safe_proxy(self) -> str | None:
        """Trả URL proxy đã ẩn user:pass cho log (không log credential)."""
        if not self.proxy:
            return None
        # Format: scheme://user:pass@host:port → scheme://***@host:port
        if "@" in self.proxy:
            scheme_split = self.proxy.split("://", 1)
            if len(scheme_split) == 2:
                scheme, rest = scheme_split
                _, _, host = rest.partition("@")
                return f"{scheme}://***@{host}"
        return self.proxy

    def _build_sync_client(self) -> httpx.Client:
        """httpx sync client kèm proxy + timeout chuẩn cho Outlook."""
        kwargs: dict[str, Any] = {"timeout": _OUTLOOK_HTTP_TIMEOUT}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return httpx.Client(**kwargs)

    async def _refresh_access(self, *, log) -> None:
        log(f"[otp:outlook] refreshing access token for {self.combo.email}"
            + (f" via proxy {self._safe_proxy()}" if self.proxy else ""))

        def _sync_refresh():
            with self._build_sync_client() as client:
                return client.post(
                    _TOKEN_URL,
                    data={
                        "client_id": self.combo.client_id,
                        "scope": self.scope,
                        "refresh_token": self.combo.refresh_token,
                        "grant_type": "refresh_token",
                    },
                )

        response = await asyncio.to_thread(_sync_refresh)
        if response.status_code != 200:
            body = response.text[:500]
            # Phân biệt fatal (combo dead) vs transient (network blip / 5xx)
            fatal = any(key in body for key in _OUTLOOK_AUTH_FATAL_KEYS)
            if fatal or 400 <= response.status_code < 500:
                raise OutlookComboError(
                    f"refresh failed HTTP {response.status_code}: {body}"
                )
            raise OutlookProviderUnavailable(
                f"refresh transient HTTP {response.status_code}: {body[:200]}"
            )
        data = response.json()
        access = data.get("access_token")
        new_refresh = data.get("refresh_token")
        if not access:
            raise OutlookComboError(f"refresh response missing access_token: {data}")
        self._access_token = access
        self._access_expires_at = time.monotonic() + max(int(data.get("expires_in", 3600)) - 60, 60)
        if new_refresh and new_refresh != self.combo.refresh_token:
            self.combo.refresh_token = new_refresh
        self._persist_state(data)

    async def _ensure_access(self, *, log) -> str:
        if self._access_token and time.monotonic() < self._access_expires_at:
            return self._access_token
        await self._refresh_access(log=log)
        assert self._access_token
        return self._access_token

    async def _list_messages(
        self,
        *,
        access_token: str,
        folder_name: str | None,
        top: int = 10,
    ) -> list[dict[str, Any]]:
        """Lấy `top` message mới nhất, optional theo tên folder."""
        def _sync_list():
            with self._build_sync_client() as client:
                if folder_name is None:
                    url = f"{_GRAPH_BASE}/me/messages"
                else:
                    # Filter folder by displayName
                    folder_resp = client.get(
                        f"{_GRAPH_BASE}/me/mailFolders",
                        params={"$filter": f"displayName eq '{folder_name}'"},
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    folder_resp.raise_for_status()
                    folders = folder_resp.json().get("value", [])
                    if not folders:
                        return []
                    folder_id = folders[0]["id"]
                    url = f"{_GRAPH_BASE}/me/mailFolders/{folder_id}/messages"

                resp = client.get(
                    url,
                    params={
                        "$top": top,
                        "$orderby": "receivedDateTime desc",
                        "$select": "subject,from,receivedDateTime,bodyPreview,body",
                    },
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                resp.raise_for_status()
                return resp.json().get("value", [])

        return await asyncio.to_thread(_sync_list)

    async def poll_otp(
        self,
        *,
        recipient: str,
        started_at: datetime,
        timeout_seconds: float,
        poll_interval_seconds: float,
        log,
    ) -> str:
        # Recipient phải khớp combo email — nếu không, OTP sẽ vào account khác.
        if recipient.strip().lower() != self.combo.email.strip().lower():
            log(
                f"[otp:outlook] WARNING recipient={recipient} != combo={self.combo.email} "
                f"— vẫn poll combo mailbox"
            )

        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        log(f"[otp:outlook] polling {self.combo.email} (timeout {timeout_seconds:.0f}s)"
            + (f" via proxy {self._safe_proxy()}" if self.proxy else " direct"))

        # _ensure_access() and _list_messages() create and close their own HTTP clients.
        if True:
            attempt = 0
            consecutive_transient = 0
            while True:
                attempt += 1
                try:
                    access = await self._ensure_access(log=log)
                    # Strategy: query toàn bộ mailbox (folder=None) để bắt mail dù
                    # ở Inbox, Junk, hoặc folder lạ. Nhanh hơn và tin cậy hơn loop folder.
                    messages = await self._list_messages(
                        access_token=access, folder_name=None,
                        top=5,
                    )
                    consecutive_transient = 0  # reset khi 1 round thành công
                    for msg in messages:
                        received = _parse_dt(msg.get("receivedDateTime"))
                        # Chỉ accept mail received SAU started_at (hoặc cùng giây).
                        # started_at đã được reset = NOW sau set password trong browser_phase,
                        # nên mail OTP cũ (trước set password) sẽ bị skip.
                        if received is not None and started_at is not None:
                            if received < started_at:
                                continue
                        sender = (
                            (msg.get("from") or {}).get("emailAddress", {}).get("address", "")
                        )
                        subject = msg.get("subject") or ""
                        body_obj = msg.get("body") or {}
                        body = body_obj.get("content") or msg.get("bodyPreview") or ""
                        code = _extract_otp(subject, body)
                        if code and (_is_openai_sender(sender) or "openai" in subject.lower()):
                            log(f"[otp:outlook] found {code} (sender={sender} attempt {attempt})")
                            return code
                        elif code:
                            log(
                                f"[otp:outlook] suspicious code {code} from {sender} "
                                f"subject={subject!r} — skip (non-OpenAI sender)"
                            )
                except (httpx.HTTPError, OutlookProviderUnavailable) as exc:
                    consecutive_transient += 1
                    # Dùng repr để bắt được cả ConnectTimeout("") không có message.
                    log(
                        f"[otp:outlook] network error attempt {attempt}"
                        f" ({consecutive_transient}/{_OUTLOOK_CONNECT_FAIL_THRESHOLD}): "
                        f"{type(exc).__name__}: {exc!r}"
                    )
                    if consecutive_transient >= _OUTLOOK_CONNECT_FAIL_THRESHOLD:
                        # Không thể kết nối Microsoft → bail nhanh thay vì chờ hết OTP timeout
                        raise OutlookProviderUnavailable(
                            f"connect Microsoft thất bại {consecutive_transient} lần liên tiếp "
                            f"(proxy={self._safe_proxy() or 'direct'}). Last error: "
                            f"{type(exc).__name__}: {exc!r}"
                        ) from exc
                except OutlookComboError as exc:
                    log(f"[otp:outlook] auth error attempt {attempt}: {exc}")
                    raise

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"OTP timeout after {timeout_seconds}s for {self.combo.email}"
                    )
                await asyncio.sleep(min(poll_interval_seconds, remaining))


# ─────────────────────────────────────────────────────────────────────
# Gmail rented provider
# ─────────────────────────────────────────────────────────────────────

_GMAIL_RENTED_OTP_VALID = re.compile(r"^\d{4,8}$")


class GmailRentedComboError(Exception):
    """Combo Gmail rented parse fail."""


class GmailRentedCombo:
    """Combo format: `email|otp_api_url`.

    otp_api_url — GET → {"otp": "123456", "updated_at": "<ISO timestamp>"}
    """

    __slots__ = ("email", "otp_api_url")

    def __init__(self, email: str, otp_api_url: str):
        self.email = email
        self.otp_api_url = otp_api_url

    @classmethod
    def parse(cls, line: str) -> "GmailRentedCombo":
        parts = line.split("|", 1)
        if len(parts) != 2:
            raise GmailRentedComboError(
                f"combo phải có 2 phần (email|otp_api_url), nhận {len(parts)}"
            )
        email, otp_api_url = (p.strip() for p in parts)
        if not email or "@" not in email:
            raise GmailRentedComboError(f"email không hợp lệ: {email!r}")
        if not otp_api_url.lower().startswith("http"):
            raise GmailRentedComboError(f"otp_api_url không hợp lệ: {otp_api_url!r}")
        return cls(email=email, otp_api_url=otp_api_url)


class GmailRentedMailProvider:
    """Gmail rented email OTP provider.

    GET otp_api_url → {"otp": "123456", "updated_at": "<ISO timestamp>"}
    Accept nếu updated_at > started_at AND otp khớp \\d{4,8}.
    """

    def __init__(self, *, combo: GmailRentedCombo):
        self.combo = combo

    async def poll_otp(
        self,
        *,
        recipient: str,
        started_at: datetime,
        timeout_seconds: float,
        poll_interval_seconds: float,
        log,
    ) -> str:
        deadline = time.monotonic() + max(timeout_seconds, 1.0)
        log(f"[otp:gmail_rented] polling {self.combo.email} via API (timeout {timeout_seconds:.0f}s)")

        attempt = 0
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            while True:
                attempt += 1
                try:
                    response = await client.get(
                        self.combo.otp_api_url,
                        headers={"Accept": "application/json"},
                    )
                    if response.status_code == 200:
                        data = response.json()
                        otp = str(data.get("otp") or "").strip()
                        updated_at = _parse_dt(data.get("updated_at"))
                        if (
                            otp
                            and _GMAIL_RENTED_OTP_VALID.match(otp)
                            and updated_at is not None
                            and updated_at > started_at
                        ):
                            log(f"[otp:gmail_rented] found {otp} (updated_at={updated_at.isoformat()} attempt {attempt})")
                            return otp
                        log(f"[otp:gmail_rented] no fresh OTP (attempt {attempt}): otp={otp!r} updated_at={updated_at}")
                    else:
                        log(f"[otp:gmail_rented] HTTP {response.status_code} attempt {attempt}")
                except (httpx.HTTPError, ValueError) as exc:
                    log(f"[otp:gmail_rented] error attempt {attempt}: {exc}")

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"OTP timeout after {timeout_seconds}s for {self.combo.email}")
                await asyncio.sleep(min(poll_interval_seconds, remaining))


# ─────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────


def build_provider_worker(
    *, logs_url: str, api_key: str | None, insecure_tls: bool = True,
) -> WorkerMailProvider:
    return WorkerMailProvider(logs_url=logs_url, api_key=api_key, insecure_tls=insecure_tls)


def build_provider_outlook(
    *, combo: str, state_dir: Path, proxy: str | None = None,
) -> OutlookMailProvider:
    parsed = OutlookCombo.parse(combo)
    return OutlookMailProvider(combo=parsed, state_dir=state_dir, proxy=proxy)


def build_provider_gmail_rented(*, combo: str) -> GmailRentedMailProvider:
    parsed = GmailRentedCombo.parse(combo)
    return GmailRentedMailProvider(combo=parsed)
