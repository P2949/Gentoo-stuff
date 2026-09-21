import json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).parents[2]
def main():
 with tempfile.TemporaryDirectory() as td:
  p=Path(td); s=p/'s'; s.write_text(json.dumps({'records':[{'cpv':'app/a-1','lane':'pgo-clang-ir','state':'pending'},{'cpv':'app/b-1','lane':'pgo-gcc','state':'pending'},{'cpv':'app/c-1','lane':'pgo-clang-ir','state':'pending'}]})); attempts=p/'attempts'; attempts.mkdir(); (attempts/'a.json').write_text(json.dumps({'cpv':'app/a-1','state':'succeeded'})); (attempts/'b.json').write_text(json.dumps({'cpv':'app/b-1','state':'failed'})); out=p/'wave.json'
  subprocess.run(['python3',str(ROOT/'scripts/optimization/pgo/schedule-generation.py'),'--package-state',str(s),'--attempts',str(attempts),'--output',str(out),'--generation-id','g1','--wave-size','1'],check=True)
  wave=json.loads(out.read_text()); assert [x['cpv'] for x in wave['packages']]==['app/c-1']; assert wave['remaining_pending']==0
 print('PASS: generation scheduler resumes and preserves attempts')
if __name__=='__main__': main()
