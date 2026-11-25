from pathlib import Path

import pytest

from scripts.required_pr_checks import REQUIRED, verify_checks


def green():
    return [{"workflow": workflow, "name": name, "state": "SUCCESS"}
            for workflow, name in sorted(REQUIRED)]


def test_all_four_real_pr_checks_are_required():
    assert len(REQUIRED) == 4
    verify_checks(green())
    for index in range(4):
        rows = green()
        rows.pop(index)
        with pytest.raises(ValueError):
            verify_checks(rows)


@pytest.mark.parametrize("state", ["PENDING", "FAILURE", "SKIPPED", "NEUTRAL", "CANCELLED", None])
def test_non_success_is_blocked(state):
    rows = green()
    rows[0]["state"] = state
    with pytest.raises(ValueError):
        verify_checks(rows)


def test_other_workflow_and_duplicate_failures_cannot_satisfy_gate():
    rows = green()
    rows[0]["workflow"] = "Other workflow"
    with pytest.raises(ValueError):
        verify_checks(rows)
    rows = green()
    rows.append(dict(rows[0], state="FAILURE"))
    with pytest.raises(ValueError):
        verify_checks(rows)


def test_promotion_requires_actual_checks_and_exact_head():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/pinned-engine-updates.yml").read_text()
    assert workflow.index("gh pr checks") < workflow.index("python -m scripts.required_pr_checks") < workflow.index("gh pr merge")
    assert '--match-head-commit "$candidate_sha"' in workflow
    assert "DOWNLOADER_AUTOMATED_RELEASES_APPROVED == 'true'" in workflow
    assert "gh pr merge --admin" not in workflow
