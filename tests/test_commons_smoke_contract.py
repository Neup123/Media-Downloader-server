from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_commons_failure_classifier_has_only_safe_categories():
    tree = ast.parse((ROOT / "tests/e2e_commons_webdav.py").read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "safe_failure_category")
    import re
    ns = {"re": re}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<classifier>", "exec"), ns)
    classify = ns["safe_failure_category"]
    for detail, expected in [
        ("HTTP 429 from a site", "source_rate_limited"),
        ("403 Forbidden", "source_access_denied"),
        ("gallery-dl exited successfully but no output file was found", "no_extractor_output"),
        ("request timed out", "timeout"),
        ("token=private-test-data", "download_or_delivery_failed"),
    ]:
        assert classify({"error": detail}) == expected


def test_commons_workflow_is_opt_in_and_has_fixed_source():
    source = (ROOT / "tests/e2e_commons_webdav.py").read_text()
    workflow = (ROOT / ".github/workflows/manual-commons-webdav.yml").read_text()
    assert 'https://commons.wikimedia.org/wiki/File:Example.jpg' in source
    assert '"extractor.wikimedia.retries": "0"' in source
    assert '"downloader.http.retries": "0"' in source
    assert 'verify_remote_files(job, manifest)' in source
    assert "workflow_dispatch:" in workflow
    assert "types: [opened]" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "test/commons-single-file-diagnostic" in workflow
    assert 'ALLOW_PRIVATE_TARGETS: "false"' in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "gh pr merge" not in workflow
