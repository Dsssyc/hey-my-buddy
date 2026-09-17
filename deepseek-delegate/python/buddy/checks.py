"""Run focused Python integration tests and the dependency-free Node suite."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'BUDDY_PYTHON':sys.executable}
    subprocess.run([sys.executable,'-m','unittest','discover','-s',str(root/'python/tests'),'-v'],env=env,check=True)
    node = env.get('BUDDY_NODE') or shutil.which('node')
    if not node: raise SystemExit('Node.js is required for the dsh process runner')
    subprocess.run([node,'--test'],cwd=root,env=env,check=True)

if __name__ == '__main__': main()
