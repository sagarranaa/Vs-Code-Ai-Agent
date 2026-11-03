"""Run a command only after the editor has obtained explicit user approval."""

import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import BinaryIO


MAX_OUTPUT_BYTES = 20_000


class CommandExecutionError(Exception):
    """A safe-to-display command execution error."""


def run_approved_command(
    argv: list[str],
    *,
    workspace_root: Path,
    timeout_seconds: int,
) -> dict[str, str | int | bool | None]:
    if not argv or len(argv) > 30:
        raise CommandExecutionError("The command argument list is empty or too long.")
    if any(not arg or "\x00" in arg for arg in argv):
        raise CommandExecutionError("Command arguments must be non-empty text without null bytes.")
    if sum(len(arg) for arg in argv) > 4_000:
        raise CommandExecutionError("The command argument list is too long.")

    output: dict[str, bytearray | bool] = {
        "stdout": bytearray(),
        "stderr": bytearray(),
        "stdout_truncated": False,
        "stderr_truncated": False,
    }
    environment = {
        key: value
        for key in (
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "TMP",
            "TEMP",
            "SYSTEMROOT",
            "WINDIR",
            "PATHEXT",
        )
        if (value := os.environ.get(key)) is not None
    }

    try:
        process = subprocess.Popen(
            argv,
            cwd=workspace_root,
            env=environment,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )
    except FileNotFoundError as error:
        raise CommandExecutionError(
            f"Command executable was not found: {argv[0]}"
        ) from error
    except OSError as error:
        raise CommandExecutionError(
            f"The command could not be started ({error.__class__.__name__})."
        ) from error

    def drain(stream: BinaryIO | None, name: str) -> None:
        if stream is None:
            return
        buffer = output[name]
        truncated_key = f"{name}_truncated"
        while chunk := stream.read(8_192):
            if not isinstance(buffer, bytearray):
                return
            remaining = MAX_OUTPUT_BYTES - len(buffer)
            if remaining > 0:
                buffer.extend(chunk[:remaining])
            if len(chunk) > remaining:
                output[truncated_key] = True
        stream.close()

    stdout_thread = threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True)
    stderr_thread = threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if process.poll() is None:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        process.wait()
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)

    return {
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout": bytes(output["stdout"]).decode("utf-8", errors="replace"),
        "stderr": bytes(output["stderr"]).decode("utf-8", errors="replace"),
        "stdout_truncated": output["stdout_truncated"],
        "stderr_truncated": output["stderr_truncated"],
    }
