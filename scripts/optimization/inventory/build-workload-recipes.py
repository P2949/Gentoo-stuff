#!/usr/bin/env python3
import argparse,json,hashlib,collections,os,re
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--manifest',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();m=json.load(open(a.manifest)); rows=[]
 for x in m['packages']:
  if x['lane']=='pgo-go':
   # Go PGO requires a sampling/pprof-producing workload.  A generic
   # --help invocation is only a smoke test and cannot create default.pgo.
   rows.append({'cpv':x['cpv'],'lane':x['lane'],'recipes':[],'state':'no-profile-producing-workload','reason':'generic entrypoint smoke tests cannot collect Go pprof data'})
   continue
  # gspell-app1 requires a configured Enchant dictionary and a terminating
  # language-aware input stream.  This installation has no dictionaries, so
  # no deterministic representative workload can produce a valid profile.
  if x['cpv'].startswith('app-text/gspell-'):
   rows.append({'cpv':x['cpv'],'lane':x['lane'],'recipes':[],'state':'no-profile-producing-workload','reason':'gspell-app1 has no configured language dictionaries on the live system'})
   continue
  # sqlhist requires a live tracefs control path and cannot run safely as a
  # deterministic userspace workload in this boundary; retain accounting as
  # an explicit terminal workload exclusion rather than accepting exit 255.
  if x['cpv'].startswith('dev-libs/libtracefs-'):
   rows.append({'cpv':x['cpv'],'lane':x['lane'],'recipes':[],'state':'no-profile-producing-workload','reason':'sqlhist requires live tracefs control access and exits 255 without it'})
   continue
  if x['cpv'].startswith('sys-apps/gentoo-functions-'):
   rows.append({'cpv':x['cpv'],'lane':x['lane'],'recipes':[],'state':'no-profile-producing-workload','reason':'consoletype requires an interactive terminal and has no deterministic standalone invocation'})
   continue
  # These Wayland utilities require a live compositor/socket even for their
  # help paths.  Running them without that session exits nonzero after the
  # package has already built successfully, so a generic smoke recipe cannot
  # produce an authenticated profile.  Keep the accounting explicit until a
  # compositor-backed workload fixture is available; never treat the failed
  # invocation as a successful profile payload.
  if x['cpv'].startswith(('gui-apps/grim-', 'gui-apps/swayidle-', 'gui-apps/swaylock-')):
   rows.append({'cpv':x['cpv'],'lane':x['lane'],'recipes':[],'state':'no-profile-producing-workload','reason':'Wayland session required; no deterministic compositor-backed workload is available in the live boundary'})
   continue
  recipes=[]
  for e in x['entrypoints']:
   p=e['path']; safe=p.startswith(('/usr/bin/','/usr/sbin/','/bin/','/sbin/')) and not os.path.islink(p)
   # Recovery-only helpers require an input archive and are not standalone
   # representative workloads. Prefer the package's normal compressor entry
   # point when both are present.
   if os.path.basename(p) in {'bzip2recover'}:
    continue
   # doas has no --help mode: it treats the option as a command-line error.
   # -L is its documented, non-destructive diagnostic action and succeeds
   # without requiring a policy file or a child command.
   if x['cpv'].startswith('app-arch/rpm2targz-') and p == '/usr/bin/rpmoffset':
    argv=[p]; allow_empty_output=True
    recipes.append({'path':p,'build_id':e['build_id'],'argv':argv,'cwd':'/','environment':{'LC_ALL':'C','LANG':'C'},'safe_path':True,'allow_empty_output':allow_empty_output,'stdin_path':'/var/lib/gentoo-optimization/workloads/rpm2targz/minimal.rpm','execution_state':'not-run'})
    continue
   # funzip and unzipsfx require archive/input context; unzip -v is the
   # successful non-destructive representative workload for this package.
   if x['cpv'].startswith('app-arch/unzip-'):
    if p != '/usr/bin/unzip':
     continue
    argv=[p,'-v']; allow_empty_output=False
   elif p == '/usr/bin/doas':
    argv=[p,'-L']; allow_empty_output=True
   # cabextract prints help text but returns status 1; --version is the
   # successful, non-destructive representative entrypoint.
   elif p == '/usr/bin/cabextract':
    argv=[p,'--version']; allow_empty_output=False
   # libarchive's BSD utilities print usage for --help but return status 1;
   # --version is a successful, non-destructive representative workload.
   elif x['cpv'].startswith('app-arch/libarchive-'):
    argv=[p,'--version']; allow_empty_output=False
   # ncompress uses its historical -V switch; --version is parsed as an
   # unknown option and exits unsuccessfully.
   elif x['cpv'].startswith('app-arch/ncompress-'):
    argv=[p,'-V']; allow_empty_output=False
   # The zip helper programs use -v for a successful version query; their
   # --help/usage paths return nonzero status (zipnote returns 16).
   elif x['cpv'].startswith('app-arch/zip-'):
    argv=[p,'-v']; allow_empty_output=False
   # argon2 requires both a minimum-length salt and password input. Use a
   # fixed, root-owned fixture so the representative workload is
   # deterministic and never prompts during profile collection.
   elif x['cpv'].startswith('app-crypt/argon2-') and p == '/usr/bin/argon2':
    argv=[p,'12345678','-id','-t','1','-m','5','-p','1']; allow_empty_output=False
    stdin_path='/var/lib/gentoo-optimization/workloads/argon2/password'
   # evtest does not implement --help and exits nonzero after printing usage;
   # --version is a successful, non-destructive workload that needs no input
   # device and still exercises the installed executable.
   elif p == '/usr/bin/evtest':
    argv=[p,'--version']; allow_empty_output=False
   # scdoc is a filter and has no standalone help/version action.  Feed a
   # deterministic minimal document so the workload exercises parsing and
   # roff emission without reading arbitrary user files.
   elif p == '/usr/bin/scdoc':
    argv=[p]; allow_empty_output=False
    stdin_path='/var/lib/gentoo-optimization/workloads/scdoc/fixture.scd'
   # paperconf uses single-letter query actions and rejects GNU-style
   # --help/--version; -h returns the configured paper height successfully.
   elif p == '/usr/bin/paperconf':
    argv=[p,'-h']; allow_empty_output=False
   # enchant-lsmod-2 uses single-dash command options; -help is its
   # successful non-destructive query while --help is rejected.
   elif p == '/usr/bin/enchant-lsmod-2':
    argv=[p,'-help']; allow_empty_output=False
   # bmake rejects standalone help flags; querying its built-in version
   # variable is a successful, non-destructive workload.
   elif p == '/usr/bin/bmake':
    argv=[p,'-V','MAKE_VERSION']; allow_empty_output=False
   # nettle-lfib-stream has no successful standalone help mode. Use the
   # package's hash utility with a fixed read-only system fixture.
   elif p == '/usr/bin/nettle-lfib-stream':
    continue
   elif p == '/usr/bin/nettle-hash':
    argv=[p,'-a','sha256','/etc/hostname']; allow_empty_output=False
   elif p == '/usr/bin/stemwords':
    argv=[p,'-l','english','-i','/etc/hostname']; allow_empty_output=False
   elif x['cpv'].startswith('app-text/hunspell-') and p not in {'/usr/bin/hunspell','/usr/bin/hunzip'}:
    continue
   elif p == '/usr/bin/hunzip':
    argv=[p,'--help']; allow_empty_output=True
   elif x['cpv'].startswith('app-text/mandoc-') and p == '/usr/bin/mandoc':
    argv=[p,'-h']; allow_empty_output=True
   elif x['cpv'].startswith('app-text/mandoc-'):
    continue
   elif p == '/usr/bin/unifdef':
    argv=[p,'-h']; allow_empty_output=False
   elif p == '/usr/bin/xxd':
    argv=[p,'-version']; allow_empty_output=False
   elif p == '/bin/chacl':
    argv=[p,'-l','/etc/hostname']; allow_empty_output=False
   elif p == '/bin/attr':
    argv=[p,'-l','/etc/hostname']; allow_empty_output=True
   elif x['cpv'].startswith('dev-util/breakpad-'):
    argv=[p,'-h']; allow_empty_output=True
   elif x['cpv'].startswith('sys-apps/lm-sensors-'):
    if p != '/usr/bin/sensors':
     continue
    argv=[p,'--help']; allow_empty_output=False
   elif x['cpv'].startswith('app-shells/dash-'):
    if p != '/bin/dash':
     continue
    argv=[p,'-c','printf dash-workload']; allow_empty_output=False
   else:
    argv=[p,'--help']; allow_empty_output=False
   if safe:
    recipe={'path':p,'build_id':e['build_id'],'argv':argv,'cwd':'/','environment':{'LC_ALL':'C','LANG':'C'},'safe_path':True,'allow_empty_output':allow_empty_output,'execution_state':'not-run'}
    if x['cpv'].startswith('app-crypt/argon2-') and p == '/usr/bin/argon2':
     recipe['stdin_path']=stdin_path
    if p in {'/usr/bin/evtest','/usr/bin/scdoc'}:
     recipe['stdin_path']=stdin_path
    recipes.append(recipe)
  rows.append({'cpv':x['cpv'],'lane':x['lane'],'recipes':recipes,'state':'recipe-ready' if recipes else 'no-runnable-entrypoint'})
 out={'record_type':'representative-workload-recipes','schema_version':1,'source_manifest':m['sha256'],'packages':rows};out['counts']=dict(collections.Counter(x['state'] for x in rows));out['recipe_count']=sum(len(x['recipes']) for x in rows);out['sha256']=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest();json.dump(out,open(a.output,'w'),sort_keys=True,indent=2);open(a.output,'a').write('\n');print(out['counts'],out['recipe_count'])
if __name__=='__main__':main()
