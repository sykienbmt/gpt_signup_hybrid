"""Preview accounts.csv summary."""
import csv

with open("accounts.csv", encoding="utf-8") as f:
    reader = csv.reader(f, delimiter="|")
    for i, row in enumerate(reader):
        if i == 0:
            print("HEADER:", row[:4])
        else:
            hotmail = row[0].split("|")[0] if row[0] else ""
            email_gpt = row[1] if len(row) > 1 else ""
            password = row[2] if len(row) > 2 else ""
            twofa = (row[3][:20] + "...") if len(row) > 3 and row[3] else "(no 2fa)"
            success = '"success":true' in (row[4] if len(row) > 4 else "")
            status = "OK" if success else "FAIL"
            print(f"  [{i}] [{status}] hotmail={hotmail} | gpt={email_gpt} | pass={password} | 2fa={twofa}")
