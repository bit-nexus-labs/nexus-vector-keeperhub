from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "adapter" / "server.py"
spec = importlib.util.spec_from_file_location("nexus_vector_local_adapter", MODULE_PATH)
assert spec is not None and spec.loader is not None
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def test_runner_command_uses_real_repo_wrapper_and_no_shell() -> None:
    command = adapter._runner_command("simulate", "anna", "SIMULATE-ANNA-example")
    assert command[0].lower().startswith("powershell")
    runner = command[command.index("-File") + 1].replace("\\", "/")
    assert runner.endswith("tools/invoke_anna_mark_leo_video_mission.ps1")
    assert "-Effect" in command
    assert "anna" in command
    assert "-Approval" in command
    assert "SIMULATE-ANNA-example" in command


def test_broadcast_requires_explicit_approval() -> None:
    try:
        adapter._runner_command("broadcast", "mark", None)
    except ValueError as exc:
        assert str(exc) == "approval_required"
    else:
        raise AssertionError("broadcast without approval must be rejected")


def test_effect_allowlist_rejects_path_like_input() -> None:
    try:
        adapter._runner_command("simulate", "../leo", "challenge")
    except ValueError as exc:
        assert str(exc) == "unknown_effect"
    else:
        raise AssertionError("untrusted effect input must never reach subprocess")


def test_runner_output_extracts_json_before_log_path() -> None:
    stdout = '{"status":"PASS","effect_ref":"anna"}\nOPERATOR_LOG_PATH=C:\\Projects\\logs\\operator_timeline.log\n'
    assert adapter._parse_runner_output(stdout, "") == {"status": "PASS", "effect_ref": "anna"}


def test_timeout_is_ambiguous(monkeypatch) -> None:
    def raise_timeout(*args, **kwargs):
        raise adapter.subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(adapter.subprocess, "run", raise_timeout)
    status, body = adapter._run_command("verify", "anna", None)
    assert status == 504
    assert body["outcome"] == "UNKNOWN"
    assert "Do not retry" in body["note"]
