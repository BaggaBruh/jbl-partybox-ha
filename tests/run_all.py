"""Runs every test script, in parallel and in its own process (they monkeypatch modules)."""
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT_SECONDS = 120


def run(script):
    start = time.monotonic()
    try:
        done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        output, ok = done.stdout + done.stderr, done.returncode == 0
    except subprocess.TimeoutExpired as e:
        output, ok = f"timed out after {TIMEOUT_SECONDS} s\n{e.stdout or ''}", False
    return script.name, ok, time.monotonic() - start, output


def main():
    scripts = sorted(HERE.glob("test_*.py"))
    with ThreadPoolExecutor(max_workers=len(scripts)) as pool:
        results = list(pool.map(run, scripts))
    for name, ok, seconds, output in results:
        print(f"{'ok  ' if ok else 'FAIL'} {name:28s} {seconds:5.1f} s")
        if not ok:
            print(output)
    return 0 if all(r[1] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
