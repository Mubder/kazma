"""Reproduce the browser kernel probe in a disposable Ubuntu WSL delegation.

Run from the repository's Windows Python. Only an owned cgroup subtree is
created by the root helper; the probe and browser run as the ordinary user.
The isolated Python/browser downloads persist in the user's cache (WSL /tmp
is ephemeral). No distro packages or host controller configuration changes.
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'kazma-core'))
from kazma_core.security.process_budget import run_bounded
from kazma_core.security.child_env import tool_child_env

SCRATCH = '/home/balfa/.cache/kazma-browser-qualification-494866d8d64245159c10ebab204aec84'
CORE = '/mnt/g/GitHubRepos/kazma/kazma-core'
PROBE = '/mnt/g/GitHubRepos/kazma/docs/audits/evidence/browser_linux_boundary_2026_10_11.py'
PIP = '/mnt/g/GitHubRepos/kazma/.venv/Lib/site-packages/pip'
SETUP = r'''import json,os,shutil,subprocess,sys,venv
from pathlib import Path
root=Path(sys.argv[1]); root.mkdir(parents=True,exist_ok=True)
runtime=root/'runtime'
if not (runtime/'bin/python').exists(): venv.EnvBuilder(with_pip=False).create(runtime)
bootstrap=root/'pip-bootstrap'; bootstrap.mkdir(exist_ok=True)
if not (bootstrap/'pip').exists(): shutil.copytree(sys.argv[2],bootstrap/'pip')
env={'PATH':'/usr/bin:/bin','HOME':str(root/'home'),'PYTHONPATH':str(bootstrap),'PLAYWRIGHT_BROWSERS_PATH':str(root/'browsers')}
Path(env['HOME']).mkdir(exist_ok=True)
python=str(runtime/'bin/python')
subprocess.run([python,'-m','pip','--disable-pip-version-check','install','playwright==1.61.0','httpx','psutil'],env=env,check=True,timeout=120)
subprocess.run([python,'-I','-m','playwright','install','chromium'],env=env,check=True,timeout=120)
print(json.dumps({'runtime':str(root),'ready':True}),flush=True)
'''
NATIVE = r'''import os,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]); packages=root/'native-packages'; packages.mkdir(exist_ok=True)
subprocess.run(['/usr/bin/apt-get','download','libnspr4','libnss3','libasound2t64'],cwd=packages,check=True,timeout=60)
libs=root/'native'; libs.mkdir(exist_ok=True)
for package in packages.glob('*.deb'): subprocess.run(['/usr/bin/dpkg-deb','-x',str(package),str(libs)],check=True,timeout=15)
binary=root/'browsers/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell'
wrapper=root/'chromium-with-native-libraries'
wrapper.write_text('#!/bin/sh\nexport LD_LIBRARY_PATH='+str(libs/'usr/lib/x86_64-linux-gnu')+'\nexec '+str(binary)+' "$@"\n')
wrapper.chmod(0o700)
print('isolated_native_libraries_ready=true',flush=True)
'''
ROOT_PROBE = r'''import os,subprocess,sys,time,uuid
from pathlib import Path
scratch=Path(sys.argv[1]); root=Path('/sys/fs/cgroup')/('kazma-browser-qualification-'+uuid.uuid4().hex)
root.mkdir(); proc=None
try:
 for name,value in [('memory.max',str(3*1024**3)),('pids.max','512'),('cpu.max','200000 100000'),('memory.oom.group','1')]: (root/name).write_text(value)
 (root/'cgroup.subtree_control').write_text('+memory +pids +cpu')
 for path in [root,root/'cgroup.procs',root/'cgroup.threads',root/'cgroup.subtree_control']: os.chown(path,1000,1000)
 runner=root/'runner'; runner.mkdir()
 env={'PATH':'/usr/bin:/bin','HOME':str(scratch/'home'),'PLAYWRIGHT_BROWSERS_PATH':str(scratch/'browsers'),
 'KAZMA_BROWSER_CGROUP_ROOT':str(root),'KAZMA_DATA_DIR':str(scratch/'data'),
 'KAZMA_HOME':str(scratch/'home'),'KAZMA_SKILLS_HOME':str(scratch/'skills')}
 gate="import os,sys; os.read(0,1)==b'1' or sys.exit(125); os.execv(sys.argv[1],sys.argv[1:])"
 command=['/usr/bin/python3','-I','-c',gate,str(scratch/'runtime/bin/python'),'-I',sys.argv[2],sys.argv[3],str(scratch)]
 proc=subprocess.Popen(command,env=env,user=1000,group=1000,stdin=subprocess.PIPE)
 (runner/'cgroup.procs').write_text(str(proc.pid)); proc.stdin.write(b'1'); proc.stdin.flush(); proc.stdin.close()
 code=proc.wait(timeout=100); print('qualification_exit='+str(code),flush=True)
 if code: sys.exit(code)
finally:
 (root/'cgroup.kill').write_text('1')
 deadline=time.monotonic()+5
 while 'populated 0' not in (root/'cgroup.events').read_text():
  if time.monotonic()>deadline: raise TimeoutError('Qualification cgroup is not empty')
  time.sleep(.01)
 if proc is not None: proc.wait(timeout=3)
 for child in root.iterdir():
  if child.is_dir(): child.rmdir()
 root.rmdir(); print('qualification_cgroup_removed=true',flush=True)
'''


def run(stage, script, args, timeout):
    result = run_bounded(['wsl.exe', '-d', 'Ubuntu', '-u', 'root' if stage == 'boundary' else 'balfa',
        '--exec', '/usr/bin/timeout', '-k', '2', str(timeout - 5), '/usr/bin/python3', '-I', '-c', script, *args],
        env=tool_child_env(), timeout=timeout, output_bytes=64 * 1024, memory_bytes=1024**3)
    output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
    log = REPO / 'docs/audits/evidence' / f'browser_linux_{stage}_2026_10_11.log'
    log.write_text(output, encoding='utf-8')
    print(json.dumps({'stage': stage, 'exit': result.returncode, 'log': str(log)}), flush=True)
    print(output[-7000:], flush=True)
    return result.returncode


if __name__ == '__main__':
    if '--setup' in sys.argv:
        sys.exit(run('setup_persistent', SETUP, [SCRATCH, PIP], 300))
    if '--native' in sys.argv:
        sys.exit(run('native', NATIVE, [SCRATCH], 90))
    sys.exit(run('boundary', ROOT_PROBE, [SCRATCH, PROBE, CORE], 125))
