"""Owned local fixtures: strict WSL browser and disposable PostgreSQL; no paid calls."""
import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'kazma-core')]


def main():
    if '--postgres' in sys.argv:
        previous = ROOT / 'docs/audits/evidence/research_postgres_2026_10_11.py'
        source = previous.read_text(encoding='utf8').replace('research_durability_postgres_2026_10_11.log', 'remaining_postgres_2026_10_12.log')
        namespace = {'__name__':'owned_postgres_fixture', '__file__':str(previous)}
        exec(compile(source,str(previous),'exec'), namespace)
        return namespace['main']()
    harness = ast.parse((ROOT / 'docs/audits/evidence/browser_linux_harness_2026_10_11.py').read_text(encoding='utf8'))
    script = next(ast.literal_eval(node.value) for node in harness.body if isinstance(node,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='ROOT_PROBE' for t in node.targets))
    drive = ROOT.drive[0].lower()
    linux_root = '/mnt/' + drive + ROOT.as_posix()[2:]
    args = ['wsl.exe','-d','Ubuntu','-u','root','--exec','/usr/bin/timeout','-k','2','115',
        '/usr/bin/python3','-I','-c',script,
        '/home/balfa/.cache/kazma-browser-qualification-494866d8d64245159c10ebab204aec84',
        linux_root+'/docs/audits/evidence/browser_linux_boundary_2026_10_11.py',linux_root+'/kazma-core']
    result = subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=125)
    output = f'exit={result.returncode}\n{result.stdout}\n{result.stderr}'
    (ROOT / 'docs/audits/evidence/remaining_linux_browser_final_2026_10_12.log').write_text(output,encoding='utf8')
    print(output)
    return result.returncode


if __name__=='__main__':
    raise SystemExit(main())
