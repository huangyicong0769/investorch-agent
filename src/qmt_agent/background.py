"""Persistent argv-based background jobs."""

from __future__ import annotations

import argparse
import errno
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from qmt_agent.config import AppConfig

_METADATA_FILENAME = "job.json"
_RESULT_FILENAME = "result.json"
_LOCK_BUSY_MESSAGE = "Another curated-data lifecycle operation is already running."

if os.name == "posix":
    import fcntl
else:
    fcntl = None


def start_job(config: AppConfig, operation: str, command: list[str], cwd: Path, *, exclusive_lock_path: Path | None = None) -> dict[str, Any]:
    """Start a detached operation and persist generic recovery metadata."""
    lock_fd = _open_exclusive_lock(exclusive_lock_path) if exclusive_lock_path is not None else None
    job_dir: Path | None = None
    stdout_log: Path | None = None
    stderr_log: Path | None = None
    ready_read = -1
    ready_write = -1
    process: subprocess.Popen[Any] | None = None
    try:
        job_id = uuid.uuid4().hex[:config["execution.background_job_id_chars"]]
        job_dir = config.durable_job_dir / job_id
        job_dir.mkdir(mode=0o700, parents=True)
        stdout_log = job_dir / "stdout.log"
        stderr_log = job_dir / "stderr.log"
        stdout_log.touch(mode=0o600)
        stderr_log.touch(mode=0o600)
        ready_read, ready_write = os.pipe()
        worker = [sys.executable, "-m", "qmt_agent.background", "--job-dir", str(job_dir), "--cwd", str(cwd.expanduser().resolve()), "--ready-fd", str(ready_read), "--log-chunk-bytes", str(config["execution.background_log_chunk_bytes"]), "--log-max-bytes", str(config["execution.background_log_max_bytes"]), "--log-retained-bytes", str(config["execution.background_log_retained_bytes"]), "--stop-timeout-seconds", str(config["execution.background_stop_timeout_seconds"])]
        if lock_fd is not None:
            worker.extend(["--lock-fd", str(lock_fd)])
        worker.extend(["--", *command])
        pass_fds = (ready_read,) if lock_fd is None else (ready_read, lock_fd)
        process = subprocess.Popen(worker, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True, pass_fds=pass_fds)
        os.close(ready_read)
        ready_read = -1
        started_at = datetime.now(UTC).isoformat()
        _write_json(job_dir / _METADATA_FILENAME, {"version": 1, "job_id": job_id, "pid": process.pid, "operation": operation, "started_at": started_at, "stdout_log": str(stdout_log), "stderr_log": str(stderr_log)})
        os.write(ready_write, b"1")
        os.close(ready_write)
        ready_write = -1
        return {"job_id": job_id, "pid": process.pid, "status": "running", "operation": operation, "stdout_log": str(stdout_log), "stderr_log": str(stderr_log)}
    except Exception:
        if ready_read >= 0:
            os.close(ready_read)
        if ready_write >= 0:
            os.close(ready_write)
        if process is not None:
            _stop_process_group(config, process)
        if stdout_log is not None:
            stdout_log.unlink(missing_ok=True)
        if stderr_log is not None:
            stderr_log.unlink(missing_ok=True)
        if job_dir is not None:
            (job_dir / _METADATA_FILENAME).unlink(missing_ok=True)
            (job_dir / _RESULT_FILENAME).unlink(missing_ok=True)
            try:
                job_dir.rmdir()
            except OSError:
                pass
        raise
    finally:
        if lock_fd is not None:
            _close_fd(lock_fd)


def _open_exclusive_lock(path: Path) -> int:
    if os.name != "posix" or fcntl is None:
        raise RuntimeError("exclusive lifecycle locks require POSIX")
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.fchmod(descriptor, 0o600)
        return descriptor
    except BlockingIOError:
        _close_fd(descriptor)
        raise RuntimeError(_LOCK_BUSY_MESSAGE) from None
    except OSError as exc:
        _close_fd(descriptor)
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise RuntimeError(_LOCK_BUSY_MESSAGE) from None
        raise


def _close_fd(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def list_jobs(config: AppConfig) -> list[dict[str, Any]]:
    """Recover generic job state from QMT-owned sidecars."""
    root = config.durable_job_dir
    if not root.is_dir():
        return []

    jobs: list[dict[str, Any]] = []
    for job_dir in root.iterdir():
        if job_dir.is_symlink() or not job_dir.is_dir():
            continue
        try:
            metadata = _validate_metadata(_read_json(job_dir / _METADATA_FILENAME), job_dir)
        except (OSError, ValueError, TypeError):
            continue
        result_path = job_dir / _RESULT_FILENAME
        result = None
        if result_path.is_file():
            try:
                result = _validate_result(_read_json(result_path))
            except (OSError, ValueError, TypeError):
                result = None
        status = "exited" if result is not None else ("running" if _is_worker(config, metadata.get("pid"), metadata["job_id"]) else "lost")
        job = {**metadata, "status": status}
        if result is not None:
            job.update(result)
        jobs.append(job)
    return sorted(jobs, key=lambda job: str(job.get("started_at", "")))


def read_job_output(config: AppConfig, job_id: str) -> dict[str, str]:
    """Return bounded output for one known job without exposing arbitrary paths."""
    if not job_id or len(job_id) > len(uuid.UUID(int=0).hex) or any(character not in "0123456789abcdef" for character in job_id):
        raise ValueError(f"Unknown background job: {job_id}")
    job_dir = config.durable_job_dir / job_id
    if job_dir.is_symlink() or not job_dir.is_dir():
        raise ValueError(f"Unknown background job: {job_id}")
    try:
        metadata = _validate_metadata(_read_json(job_dir / _METADATA_FILENAME), job_dir)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"Unknown background job: {job_id}") from exc
    if metadata.get("job_id") != job_id:
        raise ValueError(f"Unknown background job: {job_id}")
    max_chars = config["execution.background_output_tail_chars"]
    return {"stdout": _read_tail(job_dir / "stdout.log", max_chars), "stderr": _read_tail(job_dir / "stderr.log", max_chars)}


def _stop_process_group(config: AppConfig, process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=config["execution.background_stop_timeout_seconds"])
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=config["execution.background_stop_timeout_seconds"])


def _is_worker(config: AppConfig, pid: Any, job_id: str) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        return False
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=config["execution.background_status_timeout_seconds"], check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    command = result.stdout.strip()
    return result.returncode == 0 and "qmt_agent.background" in command and job_id in command


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise OSError(f"Job sidecar is not a regular file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("job sidecar must contain a JSON object")
    return value


def _validate_metadata(value: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    job_id = value.get("job_id")
    pid = value.get("pid")
    operation = value.get("operation")
    started_at = value.get("started_at")
    if value.get("version") != 1 or job_id != job_dir.name or isinstance(pid, bool) or not isinstance(pid, int) or pid < 1 or not isinstance(operation, str) or not operation or not isinstance(started_at, str):
        raise ValueError("Invalid job metadata")
    datetime.fromisoformat(started_at)
    return {"version": 1, "job_id": job_id, "pid": pid, "operation": operation, "started_at": started_at, "stdout_log": str(job_dir / "stdout.log"), "stderr_log": str(job_dir / "stderr.log")}


def _validate_result(value: dict[str, Any]) -> dict[str, Any]:
    exit_code = value.get("exit_code")
    finished_at = value.get("finished_at")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not isinstance(finished_at, str):
        raise TypeError("Invalid job result")
    datetime.fromisoformat(finished_at)
    return {"exit_code": exit_code, "finished_at": finished_at}


def _read_tail(path: Path, max_chars: int) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    with path.open("rb") as file:
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(max(0, size - max_chars))
        return file.read().decode("utf-8", errors="replace")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _run_worker(job_dir: Path, cwd: Path, command: list[str], log_chunk_bytes: int, log_max_bytes: int, log_retained_bytes: int, stop_timeout_seconds: int, lock_fd: int | None = None) -> int:
    stdout_log = job_dir / "stdout.log"
    stderr_log = job_dir / "stderr.log"
    exit_code = 1
    process: subprocess.Popen[bytes] | None = None
    try:
        process_kwargs: dict[str, Any] = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        if lock_fd is not None:
            process_kwargs["pass_fds"] = (lock_fd,)
        process = subprocess.Popen(command, **process_kwargs)
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Unable to capture background operation output")
        stdout_thread = threading.Thread(target=_pump_output, args=(process.stdout, stdout_log, log_chunk_bytes, log_max_bytes, log_retained_bytes), daemon=True)
        stderr_thread = threading.Thread(target=_pump_output, args=(process.stderr, stderr_log, log_chunk_bytes, log_max_bytes, log_retained_bytes), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        exit_code = process.wait()
        stdout_thread.join()
        stderr_thread.join()
    except BaseException as exc:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=stop_timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=stop_timeout_seconds)
        with stderr_log.open("r+b", buffering=0) as stderr:
            stderr.seek(0, os.SEEK_END)
            stderr.write(f"Unable to run background operation: {type(exc).__name__}: {exc}\n".encode())
            _trim_output(stderr, log_max_bytes, log_retained_bytes)
    finally:
        _write_json(job_dir / _RESULT_FILENAME, {"exit_code": exit_code, "finished_at": datetime.now(UTC).isoformat()})
    return exit_code


def _pump_output(stream: BinaryIO, path: Path, log_chunk_bytes: int, log_max_bytes: int, log_retained_bytes: int) -> None:
    with stream, path.open("r+b", buffering=0) as output:
        output.seek(0, os.SEEK_END)
        while True:
            chunk = stream.read(log_chunk_bytes)
            if not chunk:
                return
            output.write(chunk)
            _trim_output(output, log_max_bytes, log_retained_bytes)


def _trim_output(output: BinaryIO, log_max_bytes: int, log_retained_bytes: int) -> None:
    if output.tell() <= log_max_bytes:
        return
    output.seek(-log_retained_bytes, os.SEEK_END)
    retained = output.read()
    output.seek(0)
    output.write(retained)
    output.truncate()
    output.seek(0, os.SEEK_END)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--ready-fd", type=int, required=True)
    parser.add_argument("--log-chunk-bytes", type=int, required=True)
    parser.add_argument("--log-max-bytes", type=int, required=True)
    parser.add_argument("--log-retained-bytes", type=int, required=True)
    parser.add_argument("--stop-timeout-seconds", type=int, required=True)
    parser.add_argument("--lock-fd", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            return 2
        try:
            ready = os.read(args.ready_fd, 1)
        finally:
            os.close(args.ready_fd)
        if ready != b"1" or not (args.job_dir / _METADATA_FILENAME).is_file():
            return 2
        return _run_worker(args.job_dir, args.cwd, command, args.log_chunk_bytes, args.log_max_bytes, args.log_retained_bytes, args.stop_timeout_seconds, args.lock_fd)
    finally:
        if args.lock_fd is not None:
            _close_fd(args.lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
