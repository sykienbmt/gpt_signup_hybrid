"""Rebuild accounts.csv: chi giu row thanh cong (success=true), bo failed."""
import csv
import json

INPUT = "accounts.csv"

with open(INPUT, encoding="utf-8", newline="") as f:
    rows = list(csv.reader(f, delimiter="|"))

header = rows[0]
good_rows = []
for r in rows[1:]:
    if len(r) > 4:
        try:
            data = json.loads(r[4])
            if data.get("success"):
                good_rows.append(r)
        except Exception:
            pass

print(f"Kept {len(good_rows)} successful account(s):")
for r in good_rows:
    twofa = r[3][:15] + "..." if r[3] else "(no 2fa)"
    print(f"  {r[1]} | pass={r[2]} | 2fa={twofa}")

with open(INPUT, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter="|")
    w.writerow(header)
    w.writerows(good_rows)

print("Done. CSV cleaned.")
