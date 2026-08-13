from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RETRY_SCRIPT = PROJECT_ROOT / "scripts" / "retry-command.sh"


def _run_retry(
    tmp_path: Path,
    *,
    success_on_attempt: int | None,
    max_attempts: int = 4,
) -> tuple[subprocess.CompletedProcess[str], int, list[int]]:
    counter = tmp_path / "attempts.txt"
    sleeps = tmp_path / "sleeps.txt"
    fake_sleep = tmp_path / "fake-sleep.sh"
    fake_sleep.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$1\" >> \"$FAKE_SLEEP_LOG\"\n",
        encoding="utf-8",
    )
    fake_sleep.chmod(0o755)
    command = (
        "from pathlib import Path; import sys; "
        "path=Path(sys.argv[1]); "
        "attempt=int(path.read_text())+1 if path.exists() else 1; "
        "path.write_text(str(attempt)); "
        "success=int(sys.argv[2]); "
        "raise SystemExit(0 if success and attempt >= success else 17)"
    )
    completed = subprocess.run(
        [
            "bash",
            str(RETRY_SCRIPT),
            "--max-attempts",
            str(max_attempts),
            "--initial-delay-seconds",
            "300",
            "--max-delay-seconds",
            "600",
            "--sleep-command",
            str(fake_sleep),
            "--",
            sys.executable,
            "-c",
            command,
            str(counter),
            str(success_on_attempt or 0),
        ],
        cwd=PROJECT_ROOT,
        env={"FAKE_SLEEP_LOG": str(sleeps)},
        text=True,
        capture_output=True,
        check=False,
    )
    attempt_count = int(counter.read_text(encoding="utf-8"))
    delays = (
        [int(value) for value in sleeps.read_text(encoding="utf-8").splitlines()]
        if sleeps.exists()
        else []
    )
    return completed, attempt_count, delays


def test_retry_runs_again_after_failure_then_succeeds(tmp_path: Path) -> None:
    completed, attempts, delays = _run_retry(tmp_path, success_on_attempt=2)

    assert completed.returncode == 0
    assert attempts == 2
    assert delays == [300]


def test_retry_propagates_final_failure_after_attempt_limit(tmp_path: Path) -> None:
    completed, attempts, delays = _run_retry(tmp_path, success_on_attempt=None)

    assert completed.returncode == 17
    assert attempts == 4
    assert delays == [300, 600, 600]


def test_retry_stops_after_first_success(tmp_path: Path) -> None:
    completed, attempts, delays = _run_retry(tmp_path, success_on_attempt=1)

    assert completed.returncode == 0
    assert attempts == 1
    assert delays == []


def test_retry_wait_budget_is_bounded_and_workflow_uses_it(tmp_path: Path) -> None:
    _, _, delays = _run_retry(tmp_path, success_on_attempt=None)
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "daily-run.yml").read_text(
        encoding="utf-8"
    )

    assert sum(delays) == 1_500
    assert sum(delays) < 120 * 60
    assert "--max-attempts 4" in workflow
    assert "--initial-delay-seconds 300" in workflow
    assert "--max-delay-seconds 600" in workflow