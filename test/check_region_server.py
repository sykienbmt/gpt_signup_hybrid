"""Verify web server + manager module imports with region support."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from gpt_signup_hybrid.web.manager import (
    LinkJobManager,
    LinkJob,
    get_link_manager,
)
from gpt_signup_hybrid.payment_link import REGION_BILLING, DEFAULT_REGION

# Check LinkJobManager has region property
lm = LinkJobManager(max_concurrent=1)
assert lm.region == DEFAULT_REGION
lm.set_region("US")
assert lm.region == "US"
lm.set_region("ID")
assert lm.region == "ID"

# Check invalid region raises
try:
    lm.set_region("XX")
    assert False, "Should have raised ValueError"
except ValueError as e:
    print(f"  ValueError raised correctly: {e}")

# Check LinkJob has region field
job = LinkJob(id="test", email="a@b.com", password="x", region="IN")
d = job.to_dict()
assert d["region"] == "IN", f"Expected 'IN', got {d['region']}"

# Check default region
job2 = LinkJob(id="test2", email="b@c.com", password="y")
assert job2.region == DEFAULT_REGION

print("All server/manager region checks passed!")
print(f"  LinkJobManager.region works: True")
print(f"  LinkJob.region in to_dict(): True")
