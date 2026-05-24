"""Hybrid ChatGPT signup: browser tới hết Sentinel + curl_cffi cho OTP/create_account.

Flow:
    Phase 1 (browser, ~5s): GET /api/auth/signin/openai?login_hint=<email> →
        auth.openai.com/email-verification → đợi sentinel SDK fire (cookie oai-sc).
    Phase 2 (HTTP, ~1s):    POST /api/accounts/email-otp/validate (poll OTP từ Worker logs)
                            → POST /api/accounts/create_account
                            → GET  /api/auth/callback/openai → __Secure-next-auth.session-token
"""
from .models import SignupRequest, SignupResult, BrowserHandoff
from .signup import run_signup

__all__ = ["SignupRequest", "SignupResult", "BrowserHandoff", "run_signup"]
