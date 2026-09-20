#!/usr/bin/env python3
"""Fail closed before broad waves when the filesystem margin is unsafe."""
from __future__ import annotations
import argparse, json, shutil
from pathlib import Path

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--path', type=Path, default=Path('/'))
    p.add_argument('--minimum-bytes', type=int, default=100 * 1024**3)
    p.add_argument('--minimum-percent', type=float, default=12.0)
    p.add_argument('--report', type=Path)
    a = p.parse_args()
    usage = shutil.disk_usage(a.path)
    free_percent = usage.free * 100.0 / usage.total if usage.total else 0.0
    report = {'path': str(a.path.resolve()), 'total_bytes': usage.total,
              'used_bytes': usage.used, 'free_bytes': usage.free,
              'free_percent': free_percent,
              'minimum_bytes': a.minimum_bytes,
              'minimum_percent': a.minimum_percent,
              'state': 'pass' if usage.free >= a.minimum_bytes and free_percent >= a.minimum_percent else 'refused'}
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print(json.dumps(report, sort_keys=True))
    if report['state'] != 'pass':
        raise SystemExit('REFUSED: storage free-space floor is not satisfied')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
