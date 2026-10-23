import subprocess, sys, time
from pathlib import Path
from datetime import datetime


SCRIPTS = [
    "run_jacoco_repairllama.py",
    "rerun_repairllama_jacoco.py",
    "combine_jacoco_csv.py",
]

LOG_DIR = Path("pipeline_jacoco_logs")
LOG_DIR.mkdir(exist_ok=True)

CONTINUE_ON_ERROR = "--continue-on-error" in sys.argv


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def banner(msg: str, char: str = "=", width: int = 60):
    print(f"\n{char * width}")
    print(f"  {msg}")
    print(f"{char * width}")


def run_script(script: str, run_ts: str) -> bool:
    script_path = Path(script)
    if not script_path.exists():
        print(f"  [ERROR] File not found: {script}")
        return False

    log_file = LOG_DIR / f"{run_ts}_{script_path.stem}.log"

    banner(f"RUNNING: {script}", char="-")
    print(f"  Log: {log_file}")
    start = time.time()

    with open(log_file, "w") as log:
        log.write(f"Script : {script}\n")
        log.write(f"Started: {datetime.now().isoformat()}\n")
        log.write("=" * 60 + "\n\n")

        proc = subprocess.Popen(
            [sys.executable, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in proc.stdout:
            print(line, end="")   
            log.write(line)       

        proc.wait()

        elapsed = time.time() - start
        summary = (
            f"\n{'=' * 60}\n"
            f"Finished: {datetime.now().isoformat()}\n"
            f"Duration: {fmt_duration(elapsed)}\n"
            f"Exit code: {proc.returncode}\n"
        )
        log.write(summary)

    success = proc.returncode == 0
    status_label = "OK" if success else f"FAILED (exit {proc.returncode})"
    print(f"\n  [{status_label}]  {script}  —  {fmt_duration(elapsed)}")
    return success


def main():
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    total_start = time.time()

    banner(f"Pipeline start — {len(SCRIPTS)} scripts — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if CONTINUE_ON_ERROR:
        print("  Mode: continue on error")
    else:
        print("  Mode: stop on first failure  (--continue-on-error to override)")

    results = []   

    for i, script in enumerate(SCRIPTS, 1):
        print(f"\n[{i}/{len(SCRIPTS)}]", end="")
        t0 = time.time()
        ok = run_script(script, run_ts)
        results.append((script, ok, fmt_duration(time.time() - t0)))

        if not ok and not CONTINUE_ON_ERROR:
            print(f"\n  Pipeline stopped after failure in: {script}")
            print("  Re-run with --continue-on-error to skip failures.\n")
            break

    banner("PIPELINE SUMMARY")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    skipped = len(SCRIPTS) - len(results)

    for script, ok, dur in results:
        icon = "✓" if ok else "✗"
        print(f"  {icon}  {dur}  {script}")
    if skipped:
        for script in SCRIPTS[len(results):]:
            print(f"  -  --:--:--  {script}  (skipped)")

    print(f"\n  Passed : {passed}")
    print(f"  Failed : {failed}")
    print(f"  Skipped: {skipped}")
    print(f"  Total  : {fmt_duration(time.time() - total_start)}")
    print(f"  Logs   : {LOG_DIR}/\n")

    sys.exit(0 if failed == 0 and skipped == 0 else 1)


if __name__ == "__main__":
    main()