from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
BOT_SCRIPT = REPO_ROOT / "scripts" / "run_telegram_bot.py"
LOCK_PATH = REPO_ROOT / "state" / "telegram_bot.lock"
LOG_PATH = REPO_ROOT / "state" / "telegram_bot_restart.log"


def _powershell_exe() -> str:
    return "powershell.exe"


def _venv_python() -> Path:
    candidate = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return candidate
    return Path(sys.executable).resolve()


def _list_bot_processes() -> list[dict]:
    escaped_script = str(BOT_SCRIPT).replace("\\", "\\\\")
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "  $_.Name -eq 'python.exe' -and $_.CommandLine -and ("
        "    $_.CommandLine -match '(^|[\\\\/])scripts[\\\\/]run_telegram_bot\\.py(\\s|$)' -or "
        f"    $_.CommandLine -like '*{escaped_script}*'"
        "  )"
        "} | "
        "Select-Object ProcessId, ParentProcessId, Name, CreationDate, CommandLine | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [_powershell_exe(), "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    raw = (completed.stdout or "").strip()
    if completed.returncode != 0 or not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _stop_bot_processes(processes: list[dict]) -> None:
    seen: set[int] = set()
    ordered = sorted(
        (process for process in processes if isinstance(process, dict)),
        key=lambda item: int(item.get("ParentProcessId", 0) or 0),
        reverse=True,
    )
    for process in ordered:
        pid = int(process.get("ProcessId", 0) or 0)
        if pid <= 0:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )


def _stop_lock_owner() -> None:
    lock_pid = _read_lock_pid()
    if lock_pid <= 0:
        return
    subprocess.run(
        ["taskkill", "/PID", str(lock_pid), "/F", "/T"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    return f" {pid} " in output or output.strip().endswith(str(pid))


def _wait_for_no_bot(timeout_seconds: float = 15.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        lock_pid = _read_lock_pid()
        if lock_pid > 0 and _pid_exists(lock_pid):
            _stop_lock_owner()
        current = _list_bot_processes()
        if not current and (lock_pid <= 0 or not _pid_exists(lock_pid)):
            return True
        _stop_bot_processes(current)
        time.sleep(0.5)
    lock_pid = _read_lock_pid()
    return not _list_bot_processes() and (lock_pid <= 0 or not _pid_exists(lock_pid))


def _remove_lock_file() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _start_bot() -> subprocess.Popen:
    python_exe = _venv_python()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_handle = LOG_PATH.open("a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    return subprocess.Popen(
        [str(python_exe), str(BOT_SCRIPT)],
        cwd=str(REPO_ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )


def _root_bot_processes(processes: list[dict]) -> list[dict]:
    process_ids = {int(process.get("ProcessId", 0) or 0) for process in processes}
    roots: list[dict] = []
    for process in processes:
        parent_pid = int(process.get("ParentProcessId", 0) or 0)
        if parent_pid not in process_ids:
            roots.append(process)
    return roots


def _read_lock_pid() -> int:
    try:
        return int(LOCK_PATH.read_text(encoding="utf-8").strip().splitlines()[0].strip())
    except (OSError, IndexError, ValueError):
        return 0


def _wait_for_stable_bot(timeout_seconds: float = 10.0) -> list[dict]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        processes = _list_bot_processes()
        roots = _root_bot_processes(processes)
        if len(roots) == 1 and _read_lock_pid() > 0:
            return processes
        time.sleep(0.5)
    return _list_bot_processes()


def main() -> int:
    if not BOT_SCRIPT.exists():
        print(f"Bot launcher not found: {BOT_SCRIPT}")
        return 1

    existing = _list_bot_processes()
    lock_pid = _read_lock_pid()
    if existing or (lock_pid > 0 and _pid_exists(lock_pid)):
        print(
            f"Stopping {len(existing)} existing bot process(es)"
            + (f" and lock owner pid={lock_pid}..." if lock_pid > 0 else "...")
        )
        _stop_lock_owner()
        _stop_bot_processes(existing)
        if not _wait_for_no_bot():
            print("Restart failed: existing bot processes did not exit in time.")
            for process in _list_bot_processes():
                print(
                    f"- pid={process.get('ProcessId')} parent={process.get('ParentProcessId')} "
                    f"started={process.get('CreationDate')}"
                )
            return 1
    else:
        print("No existing bot process found.")

    _remove_lock_file()
    _start_bot()
    time.sleep(1.5)

    running = _wait_for_stable_bot()
    roots = _root_bot_processes(running)
    lock_pid = _read_lock_pid()
    if len(roots) != 1 or lock_pid <= 0:
        print("Restart failed: expected one bot process tree with a valid lock owner after restart.")
        for process in running:
            print(
                f"- pid={process.get('ProcessId')} parent={process.get('ParentProcessId')} "
                f"started={process.get('CreationDate')}"
            )
        return 1

    process = roots[0]
    print("Bot restarted successfully.")
    print(f"root_pid={process.get('ProcessId')}")
    print(f"lock_pid={lock_pid}")
    print(f"started={process.get('CreationDate')}")
    print(f"log={LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())