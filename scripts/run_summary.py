#!/usr/bin/env python3
"""Print a Markdown summary of a run (shown on the GitHub Actions run page)."""
import json
import sys
from pathlib import Path

ep = sys.argv[1] if len(sys.argv) > 1 else ""
report = Path("output") / ep / "run_report.json"
print(f"## Market Brief Daily: {ep or '(no episode)'}")
if not ep or not report.exists():
    print("Nothing produced in this run (not due yet, or already produced today).")
    sys.exit(0)
r = json.loads(report.read_text())
status = r.get("status")
print(f"**Status:** {status}" + (f" (failed at `{r['failed_stage']}`)" if r.get("failed_stage") else ""))
print("\n| Stage | Status | Seconds |\n|---|---|---|")
for s in r.get("stages", []):
    print(f"| {s['stage']} | {s['status']} | {s.get('seconds')} |")
for s in r.get("stages", []):
    if s.get("error"):
        print(f"\n**Error in {s['stage']}:** {s['error'][:600]}")
ed = r.get("editorial", {})
if ed.get("gemini_error"):
    print(f"\n**Gemini script error:** {ed['gemini_error'][:400]}")
for a in ed.get("attempts", []):
    print(f"\n**Script ({a.get('generator')}):** errors={len(a.get('errors', []))}, "
          f"warnings={len(a.get('warnings', []))}, unverified numbers={a.get('unverified_numbers')}")
q = r.get("quota_usage", {})
print(f"\n**Gemini calls:** text={q.get('gemini_text', {}).get('ok_calls')}, "
      f"tts={q.get('gemini_tts', {}).get('ok_calls')}")
for k in ("gemini_text", "gemini_tts"):
    rl = q.get(k, {}).get("rate_limit_details")
    if rl:
        print(f"\n**{k} rate limits hit:** {rl}")
print(f"\n**YouTube:** {r.get('youtube')}")
print(f"\n**Spotify:** {r.get('spotify')}")
if r.get("video"):
    print(f"\n**Video:** {r['video']}")
print("\nDownload the episode from the **Artifacts** section of this run.")
