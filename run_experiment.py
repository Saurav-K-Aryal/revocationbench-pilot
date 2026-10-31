"""Run the published pilot in a fresh directory; preserve observed evidence."""
import argparse,datetime,hashlib,json,os,platform,sys
from pathlib import Path
from pilots.common import PilotContext,write_json
from pilots.experiments.p02_revocation import run

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--out',type=Path,required=True)
 p.add_argument('--profile',choices=['smoke','pilot'],default='pilot')
 p.add_argument('--seed',type=int,default=2026)
 a=p.parse_args();out=a.out.resolve()
 if out.exists() and any(out.iterdir()):p.error('Choose a new empty --out; prior evidence is preserved.')
 pass
 result=run(PilotContext(out,seed=a.seed,profile=a.profile,timeout_seconds=120))
 result.update(seed=a.seed,profile=a.profile,run_started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
  reproduction_environment={'python':sys.version,'platform':platform.platform()},
  published_source_sha256={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(Path('pilots').rglob('*.py'))})
 write_json(out/'result.json',result)
 print(json.dumps({'status':result['status'],'metrics':result['metrics']},indent=2))
 return 0 if result['status']=='completed' else 1
if __name__=='__main__':raise SystemExit(main())
