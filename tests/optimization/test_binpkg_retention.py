#!/usr/bin/env python3
import json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]
TOOL=ROOT/'scripts/optimization/storage/prune-binpkg-cache.py'
with tempfile.TemporaryDirectory() as t:
    root=Path(t)/'pkgs'; root.mkdir()
    (root/'app-test-1.gpkg.tar.zst').write_bytes(b'pkg')
    out=Path(t)/'report.json'
    subprocess.run([sys.executable,str(TOOL),'--root',str(root),'--output',str(out)],check=True,stdout=subprocess.DEVNULL)
    d=json.loads(out.read_text()); assert d['objects'][0]['state']=='UNKNOWN'; assert d['mode']=='dry-run'
