import json
import pytest

from scripts.new_engine_release_review import SOURCES, check_engine, plan, record_reviews


def release(version):
    return {"releases": {version: [{"yanked": False, "packagetype": "bdist_wheel",
                                   "digests": {"sha256": "a" * 64}}]}}


def test_watch_covers_every_planned_engine_and_component():
    assert set(SOURCES) == {"pyLoad", "Streamlink", "N_m3u8DL-RE", "cobalt",
                            "BBDown", "musicdl", "ddddocr", "Telegram"}
    rows = plan("", "", lambda _: pytest.fail("unintegrated engine contacted upstream"))
    assert all(r["outcome"] == "not_integrated" for r in rows)


def test_prerelease_current_pin_and_stable_candidate():
    row = check_engine("pyLoad", "pyload-ng==0.5.0b3.dev102", "",
                       lambda _: release("0.5.0"))
    assert row["review_required"] and row["candidate"] == "0.5.0"


def test_binary_actual_install_pin_and_no_prerelease():
    docker = "ARG BBDOWN_VERSION=v1.2.3\n"
    row = check_engine("BBDown", "", docker, lambda _: {
        "draft": False, "prerelease": False, "tag_name": "v1.3.0"})
    assert row["review_required"]
    with pytest.raises(ValueError):
        check_engine("BBDown", "", docker, lambda _: {
            "draft": False, "prerelease": True, "tag_name": "v1.3.0-rc1"})
    rows = plan("", docker, lambda _: {"draft": True})
    assert next(r for r in rows if r["engine"] == "BBDown")["outcome"] == "discovery_failed"


def test_dedup_even_closed_review_and_never_promote():
    row = check_engine("musicdl", "musicdl==2.0.0", "", lambda _: release("2.1.0"))
    calls = []
    def existing(args, **kwargs):
        calls.append(args)
        return json.dumps([[], [{"number": 1, "state": "closed", "title": "engine-update-blocked: musicdl 2.1.0"}]])
    record_reviews([row], "https://github.com/example/run", gh=existing)
    assert len(calls) == 1 and "--paginate" in calls[0] and "state=all" in calls[0][-1]
    def missing(args, **kwargs):
        calls.append(args)
        return "[[]]" if args[1] == "api" else ""
    calls.clear()
    record_reviews([row], "https://github.com/example/run", gh=missing)
    assert len(calls) == 2 and calls[1][:3] == ["gh", "issue", "create"]
    assert "license/activation" in calls[1][-1]
    assert "Never merge or release" in calls[1][-1]


def test_pyload_prerelease_only_is_not_a_downgrade_or_discovery_failure():
    row = check_engine("pyLoad", "pyload-ng==0.5.0b3.dev102", "",
                       lambda _: release("0.5.0b4"))
    assert row["outcome"] == "no_eligible_stable_artifact"
    assert not row["review_required"]


def test_equivalent_version_on_later_page_prevents_duplicate():
    row = check_engine("BBDown", "", "ARG BBDOWN_VERSION=v1.2.3\n", lambda _: {
        "draft": False, "prerelease": False, "tag_name": "v1.3.0"})
    calls = []
    def gh(args, **kwargs):
        calls.append(args)
        return json.dumps([
            [{"title": f"Unrelated {i}"} for i in range(100)],
            [{"title": "engine-update-blocked: BBDown 1.3", "state": "closed"}],
        ])
    record_reviews([row], "", gh=gh)
    assert len(calls) == 1


def test_pr_title_does_not_suppress_review_and_same_batch_deduplicates():
    row = check_engine("musicdl", "musicdl==2.0.0", "", lambda _: release("2.1.0"))
    calls = []
    def gh(args, **kwargs):
        calls.append(args)
        if args[1] == "api":
            return json.dumps([[{"title": "engine-update-blocked: musicdl 2.1.0",
                                 "pull_request": {}}]])
        return ""
    record_reviews([row, row], "", gh=gh)
    assert len(calls) == 2
    assert calls[1][:3] == ["gh", "issue", "create"]


@pytest.mark.parametrize("payload", ["null", "[]", "[{}]", "[[{}]]", "not-json"])
def test_malformed_ledger_fails_closed_before_issue_creation(payload):
    row = check_engine("musicdl", "musicdl==2.0.0", "", lambda _: release("2.1.0"))
    calls = []
    def gh(args, **kwargs):
        calls.append(args)
        return payload
    with pytest.raises(ValueError):
        record_reviews([row], "", gh=gh)
    assert len(calls) == 1


def test_no_candidates_does_not_fetch_ledger():
    record_reviews([{"review_required": False}], "", gh=lambda *a, **k: pytest.fail("unexpected request"))
