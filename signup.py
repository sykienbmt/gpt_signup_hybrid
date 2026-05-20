"""Orchestrator: Phase 1 (browser) → poll OTP → Phase 2 (HTTP) → SignupResult."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from .browser_phase import BrowserPhaseError, run_browser_phase
from .config import load_settings, runtime_session_dir
from .http_phase import HttpPhaseError, run_http_phase
from .mail_providers import (
    MailProvider,
    OutlookComboError,
    OutlookProviderUnavailable,
    build_provider_gmail_advanced,
    build_provider_outlook,
    build_provider_worker,
)
from .models import SignupRequest, SignupResult
from .random_profile import random_profile


def _build_mail_provider(request: SignupRequest, *, settings) -> MailProvider:
    """Chọn provider theo request.mail_provider."""
    if request.mail_provider == "outlook":
        if not request.outlook_combo:
            raise ValueError("mail_provider='outlook' yêu cầu --outlook-combo")
        return build_provider_outlook(
            combo=request.outlook_combo,
            state_dir=settings.runtime_dir / "outlook_state",
            proxy=request.proxy,
        )
    if request.mail_provider == "gmail_advanced":
        if not request.gmail_api_url:
            raise ValueError("mail_provider='gmail_advanced' yêu cầu gmail_api_url")
        # URL-only mode dùng placeholder email trong SignupRequest (Pydantic validation),
        # nhưng provider cần nhận "" để pre_check biết cần resolve từ API.
        provider_email = request.email
        if provider_email == "pending@gmail-advanced.local":
            provider_email = ""
        return build_provider_gmail_advanced(
            email=provider_email,
            api_url=request.gmail_api_url,
        )
    if request.mail_provider == "worker":
        return build_provider_worker(
            logs_url=request.email_logs_url,
            api_key=request.email_api_key,
            insecure_tls=request.email_insecure_tls,
        )
    raise ValueError(f"unknown mail_provider: {request.mail_provider}")


async def run_signup(request: SignupRequest, *, log=print) -> SignupResult:
    """Chạy hybrid signup, return SignupResult."""
    settings = load_settings()

    t_total_start = time.monotonic()
    result = SignupResult(success=False, email=request.email)

    try:
        # ── Random profile nếu chưa set ──────────────────────────
        if not request.password or request.name == "ChatGPT User" or request.birthdate == "2000-01-01":
            profile = random_profile()
            if not request.password:
                request = request.model_copy(update={"password": profile["password"]})
            if request.name == "ChatGPT User":
                request = request.model_copy(update={"name": profile["name"]})
            if request.birthdate == "2000-01-01":
                request = request.model_copy(update={"birthdate": profile["birthdate"]})
            log(f"[signup] profile: name={request.name} age={profile['age']} password={request.password}")

        # ── Phase 1: browser → poll OTP → submit OTP → /about-you ──
        t_p1 = time.monotonic()
        log(f"[signup] phase 1: browser → email-verification → submit OTP → /about-you (email={request.email})")
        otp_started_at = datetime.now(timezone.utc).replace(microsecond=0)
        provider = _build_mail_provider(request, settings=settings)

        # ── Pre-check cho Gmail Advanced: verify mail_status=live trước khi mở browser ──
        if hasattr(provider, "pre_check"):
            try:
                await provider.pre_check(log=log)
            finally:
                # Luôn update email nếu provider đã resolve được (dù pre_check fail)
                if provider.email and provider.email != request.email:
                    request = request.model_copy(update={"email": provider.email})
                    result.email = provider.email
                    log(f"[signup] email updated from API: {request.email}")
            # Guard: nếu sau pre_check vẫn không có email thật → fail
            if not provider.email or provider.email == "pending@gmail-advanced.local":
                raise ValueError(
                    "Gmail Advanced: API không trả email, không thể tiếp tục signup"
                )

        handoff, otp_seconds = await run_browser_phase(
            request=request,
            settings=settings,
            mail_provider=provider,
            otp_started_at=otp_started_at,
            log=log,
        )
        result.phase1_seconds = time.monotonic() - t_p1
        result.otp_seconds = otp_seconds
        log(f"[signup] phase 1 done in {result.phase1_seconds:.2f}s (OTP {otp_seconds:.2f}s)")

        # ── Phase 2: HTTP create_account + callback ──
        t_p2 = time.monotonic()
        log(f"[signup] phase 2: HTTP create_account + callback")
        phase2_result = await run_http_phase(
            request=request, handoff=handoff, log=log,
        )
        result.phase2_seconds = time.monotonic() - t_p2
        log(f"[signup] phase 2 done in {result.phase2_seconds:.2f}s")

        result.success = True
        result.session_token = phase2_result["session_token"]
        result.access_token = phase2_result.get("access_token")
        result.user_id = phase2_result.get("user_id")
        result.account_id = phase2_result.get("account_id")
        result.cookies = phase2_result["cookies"]
        result.password = request.password
        result.name = request.name
        # Compute age
        try:
            y, m, d = request.birthdate.split("-")
            from datetime import datetime as _dt
            today = _dt.utcnow()
            result.age = today.year - int(y) - ((today.month, today.day) < (int(m), int(d)))
        except Exception:
            pass
    except (BrowserPhaseError, HttpPhaseError, TimeoutError, ValueError, OutlookComboError, OutlookProviderUnavailable) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        log(f"[signup] FAILED: {result.error}")
    except Exception as exc:  # pragma: no cover — unexpected
        result.error = f"unexpected {type(exc).__name__}: {exc}"
        log(f"[signup] UNEXPECTED FAILURE: {result.error}")
        raise
    finally:
        log(f"[signup] total {time.monotonic() - t_total_start:.2f}s")

    return result
