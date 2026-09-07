from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from investorch_qmt.config import ConfigError, default_paths, initialize_config, load_config


def run_cli(local_app_data: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = str(local_app_data)
    return subprocess.run(
        ["investorch-qmt", *args],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=30,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows deployment path contract")
def test_init_creates_valid_config_and_log_directory_without_revealing_token(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"

    completed = run_cli(local_app_data, "init")

    paths = default_paths(local_app_data / "InvestOrch" / "QMT")
    config = load_config(paths.config)
    assert completed.returncode == 0
    assert "Initialized InvestOrch QMT." in completed.stdout
    assert f"Config: {paths.config}" in completed.stdout
    assert "investorch-qmt token show" in completed.stdout
    assert config.auth.token not in completed.stdout
    assert completed.stderr == ""
    assert paths.log.parent.is_dir()
    assert len(config.auth.token) >= 32


@pytest.mark.skipif(os.name != "nt", reason="Windows deployment path contract")
def test_init_accepts_existing_directory_but_refuses_existing_config(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    root = local_app_data / "InvestOrch" / "QMT"
    root.mkdir(parents=True)

    first = run_cli(local_app_data, "init")
    original = (root / "investorch-qmt.toml").read_bytes()
    second = run_cli(local_app_data, "init")

    assert first.returncode == 0
    assert second.returncode == 1
    assert second.stdout == ""
    assert "already initialized" in second.stderr
    assert (root / "investorch-qmt.toml").read_bytes() == original


def test_failed_atomic_publish_leaves_no_partial_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = default_paths(tmp_path / "QMT")

    def fail_publish(source: str | Path, target: str | Path) -> None:
        raise OSError("simulated publication failure")

    monkeypatch.setattr("investorch_qmt.config.os.link", fail_publish)

    with pytest.raises(ConfigError, match="Cannot write configuration"):
        initialize_config(paths)

    assert not paths.config.exists()
    assert list(paths.root.glob("*.tmp")) == []


def _initialize_concurrently(root, ready, results):
    # Synchronize completed file writes at the filesystem boundary so all
    # processes have entered first-time initialization before publication.
    original_fsync = os.fsync

    def synchronized_fsync(fd):
        original_fsync(fd)
        ready.wait(timeout=30)

    os.fsync = synchronized_fsync
    try:
        config = initialize_config(default_paths(Path(root)))
        results.put(("ok", config.auth.token))
    except ConfigError as exc:
        results.put(("error", str(exc)))


def test_concurrent_initialization_publishes_exactly_one_complete_config(tmp_path: Path) -> None:
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    ready = context.Barrier(8)
    results = context.Queue()
    root = tmp_path / "QMT"
    processes = [context.Process(target=_initialize_concurrently, args=(str(root), ready, results)) for _ in range(8)]
    try:
        for process in processes:
            process.start()
        outcomes = [results.get(timeout=40) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        results.close()
        results.join_thread()

    winners = [value for status, value in outcomes if status == "ok"]
    assert len(winners) == 1
    assert all("already initialized" in value for status, value in outcomes if status == "error")
    assert load_config(default_paths(root).config).auth.token == winners[0]
    assert list(root.glob("*.tmp")) == []
