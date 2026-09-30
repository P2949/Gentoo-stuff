#!/usr/bin/env python3
import contextlib, io, json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]
TOOL=ROOT/'scripts/optimization/storage/prune-binpkg-cache.py'
spec = __import__('importlib.util').util.spec_from_file_location('binpkg_retention', TOOL)
module = __import__('importlib.util').util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as t:
    root = Path(t) / 'project'; (root / 'reports').mkdir(parents=True); (root / 'storage' / 'objects').mkdir(parents=True)
    (root / 'reports' / 'keep.json').write_text('{"snapshot-live": true}', encoding='utf-8')
    (root / 'storage' / 'objects' / 'payload.json').write_text('{"snapshot-should-not-be-scanned": true}', encoding='utf-8')
    assert module.refs([root]) == {'snapshot-live'}

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
    assert d['objects'][0]['sha256'] is None

with tempfile.TemporaryDirectory() as t:
    root=Path(t)/'pkgs'; (root/'app-test/old').mkdir(parents=True); (root/'app-test/new').mkdir(parents=True)
    (root/'app-test/old/app-test-1.0-1.gpkg.tar.zst').write_bytes(b'old')
    (root/'app-test/new/app-test-1.0-1.gpkg.tar.zst').write_bytes(b'new')
    vdb=Path(t)/'vdb/app-test/app-test-1.0'; vdb.mkdir(parents=True)
    out=Path(t)/'report.json'
    # Keep this fixture hermetic while preserving the production guard: the
    # live host may have an unrelated Portage transaction running, but this
    # subprocess is operating entirely on synthetic paths and must exercise
    # the duplicate-pruning branch.  Invoke main in-process so only the
    # fixture's activity probe is controlled; production callers still use
    # the real pgrep-based fail-closed check.
    old_argv = sys.argv
    try:
        module.active_portage = lambda: False
        sys.argv = [str(TOOL), '--root', str(root), '--vdb', str(Path(t) / 'vdb'),
                    '--output', str(out), '--prune-duplicates', '--execute',
                    '--project-lock', str(Path(t) / 'project.lock'),
                    '--generation-lock', str(Path(t) / 'generation.lock')]
        with contextlib.redirect_stdout(io.StringIO()):
            assert module.main() == 0
    finally:
        sys.argv = old_argv
    d=json.loads(out.read_text()); assert len(d['deleted']) == 1

with tempfile.TemporaryDirectory() as t:
    root=Path(t)/'pkgs'; (root/'app-test').mkdir(parents=True)
    (root/'app-test/app-test-1.0-1.gpkg.tar.zst').write_bytes(b'pkg')
    recovery=Path(t)/'recovery/binpkgs/checkpoint'; recovery.mkdir(parents=True)
    (recovery/'Packages').write_text('CPV: app-test/app-test-1.0\n', encoding='utf-8')
    out=Path(t)/'report.json'
    subprocess.run([sys.executable,str(TOOL),'--root',str(root),'--recovery-root',str(Path(t)/'recovery'),
                    '--output',str(out),'--project-lock',str(Path(t)/'project.lock'),
                    '--generation-lock',str(Path(t)/'generation.lock')],check=True,stdout=subprocess.DEVNULL)
    d=json.loads(out.read_text()); assert d['objects'][0]['state']=='RECOVERY_REQUIRED'
