"""Verify payment_link region mapping imports and works correctly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from gpt_signup_hybrid.payment_link import (
    REGION_BILLING,
    DEFAULT_REGION,
    get_checkout_url,
)

# Check region mapping
assert DEFAULT_REGION == "VN"
assert REGION_BILLING["VN"] == {"country": "VN", "currency": "VND"}
assert REGION_BILLING["ID"] == {"country": "ID", "currency": "IDR"}
assert REGION_BILLING["IN"] == {"country": "IN", "currency": "INR"}
assert REGION_BILLING["US"] == {"country": "US", "currency": "USD"}
assert len(REGION_BILLING) == 4

# Check get_checkout_url signature accepts region
import inspect
sig = inspect.signature(get_checkout_url)
assert "region" in sig.parameters
assert sig.parameters["region"].default == DEFAULT_REGION

print("All region checks passed!")
print(f"  REGION_BILLING: {REGION_BILLING}")
print(f"  DEFAULT_REGION: {DEFAULT_REGION}")
print(f"  get_checkout_url params: {list(sig.parameters.keys())}")
