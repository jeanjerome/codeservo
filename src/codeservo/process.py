from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .model import CommandResult


def run_command(
    *,
    name: str,
    command: str,
    cwd: Path,
    out_dir: Path,
    timeout_seconds: int,
) -> CommandResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / f"{name}.stdout.log"
    stderr_path = out_dir / f"{name}.stderr.log"
    started = time.monotonic()
    exit_code: int | None = None
    timed_out = False

    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            completed = subprocess.run(
                ["/bin/sh", "-lc", command],
                cwd=cwd,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout_seconds,
                check=False,
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True

    duration_ms = int((time.monotonic() - started) * 1000)
    return CommandResult(
        name=name,
        command=command,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        timed_out=timed_out,
    )


def tail(path: str | Path, lines: int = 120) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    content = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])
