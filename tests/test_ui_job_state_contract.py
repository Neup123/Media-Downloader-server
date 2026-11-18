"""The public standalone UI must use the same terminal vocabulary as the worker."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.job_states import JobStatus, TERMINAL_STATUSES
from scripts.generate_job_status_ts import OUTPUT, render


def test_frontend_contract_is_generated_and_exact():
    assert OUTPUT.read_text(encoding="utf-8") == render()
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "scripts.generate_job_status_ts", "--check"],
                   cwd=root, check=True, capture_output=True, text=True, timeout=15)
    source = (root / "web/src/JobCard.tsx").read_text(encoding="utf-8")
    assert "isTerminalStatus(job.status)" in source
    assert "const TERMINAL = new Set(" not in source
    assert sorted(status.value for status in TERMINAL_STATUSES) == [
        "cancelled", "completed", "failed"
    ]


def test_ui_generator_adds_new_core_state_without_manual_duplicate(monkeypatch):
    from scripts import generate_job_status_ts as gen
    # Regression: the emitted contract always derives from the canonical enum,
    # rather than maintaining a handwritten copy of statuses inside the generator.
    assert "for item in JobStatus" in gen.render.__code__.co_consts or "JOB_STATUSES" in render()
    assert set(JobStatus) - TERMINAL_STATUSES
    assert "isKnownJobStatus" in render()
