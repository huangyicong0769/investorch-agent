"""Persistent argv-based background jobs."""

from __future__ import annotations

import argparse
import errno
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from qmt_agent.config import AppConfig

_METADATA_FILENAME = "job.json"
_RESULT_FILENAME = "result.json"
_STOP_LOCK_FILENAME = ".stop.lock"
_LOCK_BUSY_MESSAGE = "Another curated-data lifecycle operation is already running."
_EXITED_STATUS = "exited"
_STOPPED_STATUS = "stopped"
_STOP_FAILED_STATUS = "stop_failed"
_STOP_SIGNALS = {"SIGTERM": signal.SIGTERM, "SIGKILL": signal.SIGKILL}

if os.name == "posix":
    import fcntl
else:
    fcntl = None


def start_job(config: AppConfig, operation: str, command: list[str], cwd: Path, *, exclusive_lock_path: Path | None = None) -> dict[str, Any]:
    """Start a detached operation and persist generic recovery metadata."""
    lock_path = Path(exclusive_lock_path).expanduser().resolve() if exclusive_lock_path is not None else None
    lock_fd = _open_exclusive_lock(lock_path) if lock_path is not None else None
    job_dir: Path | None = None
    stdout_log: Path | None = None
    stderr_log: Path | None = None
    ready_read = -1
    ready_write = -1
    process: subprocess.Popen[Any] | None = None
    try:
        job_id = uuid.uuid4().hex[:config["execution.background_job_id_chars"]]
        worker_token = secrets.token_hex(16)
        job_dir = config.durable_job_dir / job_id
        job_dir.mkdir(mode=0o700, parents=True)
        stdout_log = job_dir / "stdout.log"
        stderr_log = job_dir / "stderr.log"
        stdout_log.touch(mode=0o600)
        stderr_log.touch(mode=0o600)
        ready_read, ready_write = os.pipe()
        worker = [sys.executable, "-m", "qmt_agent.background", "--job-dir", str(job_dir), "--cwd", str(cwd.expanduser().resolve()), "--ready-fd", str(ready_read), "--log-chunk-bytes", str(config["execution.background_log_chunk_bytes"]), "--log-max-bytes", str(config["execution.background_log_max_bytes"]), "--log-retained-bytes", str(config["execution.background_log_retained_bytes"]), "--stop-timeout-seconds", str(config["execution.background_stop_timeout_seconds"]), "--worker-token", worker_token]
        if lock_fd is not None:
            worker.extend(["--lock-fd", str(lock_fd)])
        worker.extend(["--", *command])
        pass_fds = (ready_read,) if lock_fd is None else (ready_read, lock_fd)
        process = subprocess.Popen(worker, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True, pass_fds=pass_fds)
        os.close(ready_read)
        ready_read = -1
        started_at = datetime.now(UTC).isoformat()
        metadata = {"version": 1, "job_id": job_id, "pid": process.pid, "pgid": process.pid, "operation": operation, "started_at": started_at, "stdout_log": str(stdout_log), "stderr_log": str(stderr_log), "worker_token": worker_token}
        if lock_path is not None:
            metadata["exclusive_lock_path"] = str(lock_path)
        _write_json(job_dir / _METADATA_FILENAME, metadata)
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
        result = _read_result(job_dir)
        public_metadata = _public_metadata(metadata)
        status = result["status"] if result is not None else (_is_worker(config, metadata, job_dir) and "running" or "lost")
        job = {**public_metadata, "status": status}
        if result is not None:
            job.update(result)
        elif status == "lost" and metadata.get("pgid_explicit") and _group_exists(metadata["pgid"], config["execution.background_status_timeout_seconds"]):
            job["orphaned_process_group"] = True
        jobs.append(job)
    return sorted(jobs, key=lambda job: str(job.get("started_at", "")))


def read_job_output(config: AppConfig, job_id: str) -> dict[str, Any]:
    """Return bounded output for one known job without exposing arbitrary paths."""
    job_dir = _job_dir_for_id(config, job_id)
    try:
        metadata = _validate_metadata(_read_json(job_dir / _METADATA_FILENAME), job_dir)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"Unknown background job: {job_id}") from exc
    if metadata.get("job_id") != job_id:
        raise ValueError(f"Unknown background job: {job_id}")
    max_chars = config["execution.background_output_tail_chars"]
    output: dict[str, Any] = {"stdout": _read_tail(job_dir / "stdout.log", max_chars), "stderr": _read_tail(job_dir / "stderr.log", max_chars)}
    result = _read_result(job_dir)
    if result is not None:
        output.update(result)
    return output


def stop_job(config: AppConfig, job_id: str) -> dict[str, Any]:
    """Stop one durable job's process group and persist a truthful termination receipt."""
    job_dir = _job_dir_for_id(config, job_id)
    stop_lock = _open_stop_lock(job_dir)
    try:
        return _stop_job_locked(config, job_id, job_dir)
    finally:
        _close_fd(stop_lock)


def _open_stop_lock(job_dir: Path) -> int:
    if os.name != "posix" or fcntl is None:
        raise RuntimeError("durable job stop locks require POSIX")
    descriptor = os.open(job_dir / _STOP_LOCK_FILENAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except BaseException:
        _close_fd(descriptor)
        raise


def _stop_job_locked(config: AppConfig, job_id: str, job_dir: Path) -> dict[str, Any]:
    try:
        raw_metadata = _read_json(job_dir / _METADATA_FILENAME)
        metadata = _validate_metadata(raw_metadata, job_dir)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"Unknown background job: {job_id}") from exc
    result = _read_result(job_dir)
    lock_released = _probe_exclusive_lock(config, metadata)
    if result is not None:
        if not lock_released:
            return _stop_failure(job_id, "exclusive lock remains held", result=result, group_terminated=result.get("group_terminated", True), lock_released=False)
        if result["status"] == _STOPPED_STATUS:
            return {"job_id": job_id, **result, "already_stopped": True, "lock_released": True}
        return {"job_id": job_id, **result, "status": "already_exited", "lock_released": True}

    pgid = metadata["pgid"]
    if not _safe_process_group(metadata):
        raise ValueError(f"Invalid background process group for job: {job_id}")
    snapshot = _process_snapshot(config, metadata["pid"])
    status_timeout = config["execution.background_status_timeout_seconds"]
    group_exists = _group_exists(pgid, status_timeout)
    if snapshot is not None:
        if not _matches_worker(snapshot, metadata, job_dir):
            raise RuntimeError(f"Background job identity mismatch; refusing to stop PID reuse: {job_id}")
        if snapshot["pgid"] != pgid or snapshot["pgid"] != snapshot["pid"]:
            raise RuntimeError(f"Background job process-group identity mismatch: {job_id}")
    elif group_exists and not metadata.get("pgid_explicit"):
        return _stop_failure(job_id, "legacy job metadata does not prove the process-group identity", group_terminated=False)

    if not group_exists:
        result = _read_result(job_dir)
        if result is not None:
            if not lock_released:
                return _stop_failure(job_id, "exclusive lock remains held", result=result, group_terminated=True, lock_released=False)
            if result["status"] == _STOPPED_STATUS:
                return {"job_id": job_id, **result, "already_stopped": True, "lock_released": True}
            return {"job_id": job_id, **result, "status": "already_exited", "lock_released": True}
        if not lock_released:
            return _stop_failure(job_id, "exclusive lock remains held after process-group disappearance", group_terminated=True, lock_released=False)
        return {"job_id": job_id, "status": "lost", "group_terminated": True, "lock_released": lock_released}

    escalated = False
    termination = "SIGTERM"
    try:
        _signal_process_group(pgid, _STOP_SIGNALS[termination])
    except RuntimeError as exc:
        if _group_exists(pgid, status_timeout):
            return _stop_failure(job_id, str(exc), group_terminated=False)
    group_terminated = _wait_process_group_gone(pgid, config["execution.background_stop_timeout_seconds"], status_timeout)
    if not group_terminated:
        if _group_exists(pgid, status_timeout):
            escalated = True
            termination = "SIGKILL"
            try:
                _signal_process_group(pgid, _STOP_SIGNALS[termination])
            except RuntimeError as exc:
                if _group_exists(pgid, status_timeout):
                    return _stop_failure(job_id, str(exc), termination=termination, escalated=escalated, group_terminated=False)
            group_terminated = _wait_process_group_gone(pgid, config["execution.background_stop_timeout_seconds"], status_timeout)
        else:
            group_terminated = True
    _reap_worker(metadata["pid"], config["execution.background_stop_timeout_seconds"])
    if not group_terminated:
        return _stop_failure(job_id, "process group did not terminate", termination=termination, escalated=escalated, group_terminated=False)

    lock_released = _probe_exclusive_lock(config, metadata)
    if not lock_released:
        return _stop_failure(job_id, "exclusive lock remains held after process-group termination", termination=termination, escalated=escalated, group_terminated=True, lock_released=False)
    result = _read_result(job_dir)
    if result is not None:
        if result["status"] == _STOPPED_STATUS:
            return {"job_id": job_id, **result, "already_stopped": True, "lock_released": True}
        return {"job_id": job_id, **result, "status": "already_exited", "lock_released": True}
    receipt = {"status": _STOPPED_STATUS, "exit_code": None, "finished_at": datetime.now(UTC).isoformat(), "termination": termination, "escalated": escalated, "group_terminated": True}
    _write_json(job_dir / _RESULT_FILENAME, receipt)
    return {"job_id": job_id, **receipt, "lock_released": True}


def _stop_process_group(config: AppConfig, process: subprocess.Popen[Any]) -> None:
    status_timeout = config["execution.background_status_timeout_seconds"]
    if os.name != "posix" or not _group_exists(process.pid, status_timeout):
        try:
            process.wait(timeout=0)
        except (subprocess.TimeoutExpired, ChildProcessError):
            pass
        return
    try:
        _signal_process_group(process.pid, signal.SIGTERM)
        if not _wait_process_group_gone(process.pid, config["execution.background_stop_timeout_seconds"], status_timeout):
            if _group_exists(process.pid, status_timeout):
                _signal_process_group(process.pid, signal.SIGKILL)
                _wait_process_group_gone(process.pid, config["execution.background_stop_timeout_seconds"], status_timeout)
    except (OSError, RuntimeError):
        pass
    finally:
        try:
            process.wait(timeout=config["execution.background_stop_timeout_seconds"])
        except (subprocess.TimeoutExpired, ChildProcessError):
            pass


def _is_worker(config: AppConfig, metadata: dict[str, Any], job_dir: Path) -> bool:
    snapshot = _process_snapshot(config, metadata["pid"])
    return snapshot is not None and _matches_worker(snapshot, metadata, job_dir)


def _process_snapshot(config: AppConfig, pid: int) -> dict[str, Any] | None:
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "pid=,pgid=,command="], capture_output=True, text=True, timeout=config["execution.background_status_timeout_seconds"], check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split(None, 2)
    if len(fields) != 3:
        return None
    try:
        snapshot_pid = int(fields[0])
        snapshot_pgid = int(fields[1])
    except ValueError:
        return None
    return {"pid": snapshot_pid, "pgid": snapshot_pgid, "command": fields[2]}


def _matches_worker(snapshot: dict[str, Any], metadata: dict[str, Any], job_dir: Path) -> bool:
    if snapshot["pid"] != metadata["pid"] or snapshot["pgid"] != metadata["pgid"]:
        return False
    command = snapshot["command"]
    if "-m qmt_agent.background" not in command:
        return False
    job_marker = f"--job-dir {job_dir} --cwd "
    if job_marker not in command:
        return False
    worker_token = metadata.get("worker_token")
    if worker_token is None:
        return True
    return f"--worker-token {worker_token}" in command


def _safe_process_group(metadata: dict[str, Any]) -> bool:
    pid = metadata["pid"]
    pgid = metadata["pgid"]
    if os.name != "posix" or isinstance(pgid, bool) or not isinstance(pgid, int) or pgid <= 1 or pgid != pid:
        return False
    return pgid not in {os.getpid(), os.getpgrp()}


def _group_exists(pgid: int, probe_timeout_seconds: int | float) -> bool:
    if os.name != "posix":
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        try:
            result = subprocess.run(["ps", "-axo", "pgid="], capture_output=True, text=True, timeout=probe_timeout_seconds, check=False)
        except (OSError, subprocess.SubprocessError):
            return True
        return any(line.strip() == str(pgid) for line in result.stdout.splitlines())
    return True


def _signal_process_group(pgid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        raise RuntimeError(f"Unable to signal background process group {pgid}: {exc}") from exc


def _wait_process_group_gone(pgid: int, timeout_seconds: int | float, probe_timeout_seconds: int | float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while _group_exists(pgid, probe_timeout_seconds):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _reap_worker(pid: int, timeout_seconds: int) -> bool:
    if os.name != "posix":
        return False
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, ProcessLookupError):
            return False
        if waited_pid == pid:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _probe_exclusive_lock(config: AppConfig, metadata: dict[str, Any]) -> bool:
    raw_path = metadata.get("exclusive_lock_path")
    if raw_path is None:
        return True
    try:
        path = Path(raw_path).expanduser()
        state_dir = config.state_dir.resolve()
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError, TypeError):
        return False
    if not path.is_absolute() or path.is_symlink() or not resolved.is_relative_to(state_dir):
        return False
    if not path.exists():
        return True
    if not path.is_file():
        return False
    try:
        descriptor = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        if fcntl is None:
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return False
        return True
    finally:
        _close_fd(descriptor)


def _stop_failure(job_id: str, error: str, *, result: dict[str, Any] | None = None, termination: str | None = None, escalated: bool | None = None, group_terminated: bool = False, lock_released: bool | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {"job_id": job_id, "status": _STOP_FAILED_STATUS, "error": error, "group_terminated": group_terminated}
    if termination is not None:
        response["termination"] = termination
    if escalated is not None:
        response["escalated"] = escalated
    if lock_released is not None:
        response["lock_released"] = lock_released
    if result is not None:
        response["existing_status"] = result["status"]
    return response


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise OSError(f"Job sidecar is not a regular file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("job sidecar must contain a JSON object")
    return value


def _job_dir_for_id(config: AppConfig, job_id: str) -> Path:
    if not isinstance(job_id, str) or not job_id or len(job_id) > len(uuid.UUID(int=0).hex) or any(character not in "0123456789abcdef" for character in job_id):
        raise ValueError(f"Unknown background job: {job_id}")
    job_dir = config.durable_job_dir / job_id
    if job_dir.parent != config.durable_job_dir or job_dir.is_symlink() or not job_dir.is_dir():
        raise ValueError(f"Unknown background job: {job_id}")
    return job_dir


def _read_result(job_dir: Path) -> dict[str, Any] | None:
    result_path = job_dir / _RESULT_FILENAME
    if not result_path.is_file() or result_path.is_symlink():
        return None
    try:
        return _validate_result(_read_json(result_path))
    except (OSError, ValueError, TypeError):
        return None


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: metadata[key] for key in ("version", "job_id", "pid", "pgid", "operation", "started_at", "stdout_log", "stderr_log")}


def _validate_metadata(value: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    job_id = value.get("job_id")
    pid = value.get("pid")
    operation = value.get("operation")
    started_at = value.get("started_at")
    pgid = value.get("pgid", pid)
    worker_token = value.get("worker_token")
    lock_path = value.get("exclusive_lock_path")
    if value.get("version") != 1 or job_id != job_dir.name or isinstance(pid, bool) or not isinstance(pid, int) or pid < 1 or isinstance(pgid, bool) or not isinstance(pgid, int) or pgid < 1 or not isinstance(operation, str) or not operation or not isinstance(started_at, str) or (worker_token is not None and (not isinstance(worker_token, str) or not worker_token)) or (lock_path is not None and (not isinstance(lock_path, str) or not Path(lock_path).expanduser().is_absolute())):
        raise ValueError("Invalid job metadata")
    datetime.fromisoformat(started_at)
    return {"version": 1, "job_id": job_id, "pid": pid, "pgid": pgid, "pgid_explicit": "pgid" in value, "operation": operation, "started_at": started_at, "stdout_log": str(job_dir / "stdout.log"), "stderr_log": str(job_dir / "stderr.log"), "worker_token": worker_token, "exclusive_lock_path": lock_path}


def _validate_result(value: dict[str, Any]) -> dict[str, Any]:
    status = value.get("status", _EXITED_STATUS)
    exit_code = value.get("exit_code")
    finished_at = value.get("finished_at")
    if status not in {_EXITED_STATUS, _STOPPED_STATUS} or not isinstance(finished_at, str) or (status == _EXITED_STATUS and (isinstance(exit_code, bool) or not isinstance(exit_code, int))) or (status == _STOPPED_STATUS and exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int))):
        raise TypeError("Invalid job result")
    datetime.fromisoformat(finished_at)
    result = {"status": status, "exit_code": exit_code, "finished_at": finished_at}
    if status == _STOPPED_STATUS:
        termination = value.get("termination")
        escalated = value.get("escalated")
        group_terminated = value.get("group_terminated")
        if termination not in _STOP_SIGNALS or not isinstance(escalated, bool) or not isinstance(group_terminated, bool):
            raise TypeError("Invalid stopped job result")
        result.update({"termination": termination, "escalated": escalated, "group_terminated": group_terminated})
    return result


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
    parser.add_argument("--worker-token")
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
