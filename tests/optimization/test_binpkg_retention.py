#!/usr/bin/env python3
import json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]
TOOL=ROOT/'scripts/optimization/storage/prune-binpkg-cache.py'
with tempfile.TemporaryDirectory() as t:
    root=Path(t)/'pkgs'; root.mkdir()
    (root/'app-test').mkdir()
    (root/'app-test/app-test-1.gpkg.tar.zst').write_bytes(b'pkg')
    out=Path(t)/'report.json'
    subprocess.run([sys.executable,str(TOOL),'--root',str(root),'--output',str(out),
                    '--project-lock',str(Path(t)/'project.lock'),
                    '--generation-lock',str(Path(t)/'generation.lock'),
                    '--reference-root',str(Path(t)/'no-references')],check=True,stdout=subprocess.DEVNULL)
    d=json.loads(out.read_text()); assert d['objects'][0]['state']=='UNKNOWN'; assert d['mode']=='dry-run'

with tempfile.TemporaryDirectory() as t:
    root=Path(t)/'pkgs'; (root/'app-test/old').mkdir(parents=True); (root/'app-test/new').mkdir(parents=True)
    (root/'app-test/old/app-test-1.gpkg.tar.zst').write_bytes(b'old')
    (root/'app-test/new/app-test-1.gpkg.tar.zst').write_bytes(b'new')
    vdb=Path(t)/'vdb/app-test/app-test-1'; vdb.mkdir(parents=True)
    out=Path(t)/'report.json'
    subprocess.run([sys.executable,str(TOOL),'--root',str(root),'--vdb',str(Path(t)/'vdb'),
                    '--output',str(out),'--prune-duplicates','--execute',
                    '--project-lock',str(Path(t)/'project.lock'),
                    '--generation-lock',str(Path(t)/'generation.lock')],check=True,stdout=subprocess.DEVNULL)
    d=json.loads(out.read_text()); assert len(d['deleted']) == 1
