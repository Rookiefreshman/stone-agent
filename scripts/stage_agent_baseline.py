"""Stage the full, local Stone Agent source in a CLEAN, correct Git checkout.
No automatic commits, network requests, force updates, deletes or pushes.
"""
from pathlib import Path
import argparse, shutil, subprocess

BRANCH='agent/cancel-rehearsal-v0.9'
REMOTES={'https://github.com/Rookiefreshman/stone-agent.git','https://github.com/Rookiefreshman/stone-agent','git@github.com:Rookiefreshman/stone-agent.git','ssh://git@github.com/Rookiefreshman/stone-agent.git'}
ROOT=Path(__file__).resolve().parents[1]
TOP={'README.md','requirements.txt','alembic.ini','.gitignore','TEST_RESULTS.txt'}
DIRS={'agent','tests','migrations','.github','docs','patches','scripts'}
ALLOWED_SUFFIXES={'.py','.sql','.md','.yml','.yaml','.ini','.patch','.txt'}

def sources(root):
    return sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
        and (p.relative_to(root).as_posix() in TOP or
             (p.relative_to(root).parts[0] in DIRS and p.suffix in ALLOWED_SUFFIXES))
        and not set(p.relative_to(root).parts)&{'.git','__pycache__','.venv','.pytest_cache'})

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout',type=Path,required=True)
    parser.add_argument('--expected-head',required=True,help='Exact reviewed remote branch commit')
    args=parser.parse_args(); dst=args.checkout.resolve()
    def git(*cmd): return subprocess.check_output(['git','-C',str(dst),*cmd],text=True).strip()
    if Path(git('rev-parse','--show-toplevel')).resolve()!=dst: raise SystemExit('checkout must be repository root')
    if dst==ROOT or dst in ROOT.parents or ROOT in dst.parents: raise SystemExit('source and target must be separate')
    if git('remote','get-url','origin') not in REMOTES: raise SystemExit('unexpected origin')
    if git('branch','--show-current')!=BRANCH: raise SystemExit('wrong development branch')
    if git('rev-parse','HEAD')!=args.expected_head: raise SystemExit('branch head changed; inspect before import')
    if git('status','--porcelain','--untracked-files=all'): raise SystemExit('checkout must be clean')
    files=sources(ROOT)
    for source in files:
        rel=source.relative_to(ROOT); target=dst/rel
        if any(p.is_symlink() for p in [target,*target.parents] if p!=dst.parent): raise SystemExit(f'symlink destination: {rel}')
        if not target.resolve().is_relative_to(dst): raise SystemExit(f'unsafe target: {rel}')
    for source in files:
        rel=source.relative_to(ROOT); target=dst/rel
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(source,target)
        subprocess.run(['git','-C',str(dst),'add','--',rel.as_posix()],check=True)
    print(git('diff','--cached','--stat'))
    print('Review staged diff; run tests; commit and push normally. No remote changes have been made by this script.')

if __name__=='__main__': main()
