"""Contract tests for secondary lifecycle state boundaries."""
import asyncio
from pathlib import Path

from app.completion import inspect_workspace_for_recovery, save_completed_files
from app.worker import JobWorker


def test_workspace_recovery_is_explicit_and_never_trusts_partial_files(tmp_path):
    workspace = tmp_path / "job"
    assert inspect_workspace_for_recovery(workspace) == ("empty", [])
    workspace.mkdir()
    assert inspect_workspace_for_recovery(workspace) == ("empty", [])
    file = workspace / "partial.mp4"
    file.write_bytes(b"partial")
    assert inspect_workspace_for_recovery(workspace) == ("unverified", [])
    save_completed_files(workspace, [file])
    state, files = inspect_workspace_for_recovery(workspace)
    assert state == "verified" and files == [file.resolve()]
    file.write_bytes(b"corrupted")
    assert inspect_workspace_for_recovery(workspace) == ("unverified", [])


def test_worker_task_binding_does_not_depend_on_task_name(tmp_path):
    async def probe():
        worker = JobWorker(None, None)
        current = asyncio.current_task()
        worker._active.add(current)
        worker._task_jobs[current] = "real-uuid"
        worker._active_engines["real-uuid"] = "generic"
        assert list(worker._task_jobs.values()) == ["real-uuid"]
        worker._task_done("real-uuid", current)
        assert not worker._active and not worker._task_jobs and not worker._active_engines
    asyncio.run(probe())


def test_code_has_one_explicit_recovery_branch_and_no_task_name_parsing():
    worker = (Path(__file__).resolve().parents[1] / "app/worker.py").read_text()
    assert "recovery_state, existing_files = inspect_workspace_for_recovery(workdir)" in worker
    assert 'if recovery_state == "unverified":' in worker
    assert "task.get_name()" not in worker
    assert "_task_jobs[task] = job_id" in worker


def test_available_flag_cannot_be_statically_true_for_all_engines():
    main = (Path(__file__).resolve().parents[1] / "app/main.py").read_text()
    assert '"available": executable is not None and version not in (None, "unknown")' in main
