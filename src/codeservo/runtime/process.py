from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from ..domain.results import CommandResult
from ..evidence.digests import sha256_file
from .confinement import confined
from .sandbox import Isolation


def run_command(
    *,
    name: str,
    command: str,
    cwd: Path,
    out_dir: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
    unset_env: tuple[str, ...] = (),
    isolation: Isolation = Isolation(),
) -> CommandResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = out_dir / f"{name}.stdout.log"
    stderr_path = out_dir / f"{name}.stderr.log"
    started = time.monotonic()
    process_env = os.environ.copy()
    for variable in unset_env:
        process_env.pop(variable, None)
    if env:
        process_env.update(env)
    exit_code: int | None = None
    timed_out = False

    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            with confined(["/bin/sh", "-lc", command], isolation) as application:
                completed = subprocess.run(
                    application.command,
                    cwd=cwd,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout_seconds,
                    check=False,
                    env=process_env,
                    pass_fds=application.pass_fds,
                )
                exit_code = completed.returncode
                # A command that never ran measured nothing, so its exit code
                # is not a verdict about the tree and the run stops here.
                application.confirm(exit_code, stderr_path)
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
        stdout_sha256=sha256_file(stdout_path),
        stderr_sha256=sha256_file(stderr_path),
        timed_out=timed_out,
    )


def tail(path: str | Path, lines: int = 120) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    content = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])
