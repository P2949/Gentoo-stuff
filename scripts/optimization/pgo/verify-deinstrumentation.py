#!/usr/bin/env python3
"""Verify a live instrumentation census is clean before clearing its marker."""
from __future__ import annotations
import argparse, json
from pathlib import Path
def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--census',type=Path,required=True); a=ap.parse_args()
    d=json.loads(a.census.read_text()); records=d.get('records',d.get('artifacts',[])); bad=[r for r in records if r.get('instrumentation_markers')]
    if bad:
        print(f'REFUSED: {len(bad)} instrumented records remain')
        return 1
    print('PASS: live instrumentation census is clean')
    return 0
if __name__=='__main__': raise SystemExit(main())
