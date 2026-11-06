"""Each engine's version is checked with its documented CLI flag."""
from types import SimpleNamespace


def test_get_sauce_version_flag_and_errors_do_not_look_like_versions(client, auth_headers, monkeypatch):
    observed = []

    def which(binary):
        return "/tools/" + binary

    def run(argv, **kwargs):
        observed.append(argv)
        if argv[0].endswith("/get-sauce"):
            return SimpleNamespace(returncode=0, stdout="get-sauce v1.10.0\n", stderr="")
        if argv[0].endswith("/aria2c"):
            return SimpleNamespace(returncode=2, stdout="", stderr="unknown flag\n")
        return SimpleNamespace(returncode=0, stdout="test version\n", stderr="")

    monkeypatch.setattr("app.main.shutil.which", which)
    monkeypatch.setattr("app.main.subprocess.run", run)

    response = client.get("/api/v1/engines", headers=auth_headers)
    assert response.status_code == 200
    found = {engine["name"]: engine for engine in response.json()}
    assert found["get-sauce"]["version"] == "get-sauce v1.10.0"
    assert found["aria2c"]["version"] == "unknown"
    assert ["/tools/get-sauce", "-v"] in observed
    assert ["/tools/yt-dlp", "--version"] in observed


def test_get_sauce_blank_cli_output_uses_go_module_version_from_built_image(
        client, auth_headers, monkeypatch, tmp_path):
    """Actual Go builds can return 0 with empty -v stdout/stderr."""
    from app import main

    (tmp_path / "get-sauce.version").write_text("v1.10.0\n")
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main.shutil, "which", lambda binary: (
        "/usr/local/bin/get-sauce" if binary == "get-sauce" else "/usr/bin/" + binary
    ))
    monkeypatch.setattr(main.subprocess, "run", lambda argv, **kw:
                        SimpleNamespace(returncode=0, stdout="  \n", stderr="\n")
                        if argv[0].endswith("/get-sauce") else
                        SimpleNamespace(returncode=0, stdout="2026.10.26\n", stderr=""))
    response = client.get("/api/v1/engines", headers=auth_headers)
    assert response.status_code == 200
    versions = {item["name"]: item["version"] for item in response.json()}
    assert versions["get-sauce"] == "v1.10.0 (Go module)"
    assert versions["yt-dlp"] == "2026.10.26"


def test_empty_get_sauce_version_never_becomes_blank_or_mislabels_other_binary(
        client, auth_headers, monkeypatch, tmp_path):
    from app import main
    (tmp_path / "get-sauce.version").write_text("v1.10.0\n")
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main.shutil, "which", lambda binary: "/custom/" + binary)
    monkeypatch.setattr(main.subprocess, "run", lambda argv, **kw:
                        SimpleNamespace(returncode=0, stdout="\n", stderr="\n"))
    response = client.get("/api/v1/engines", headers=auth_headers)
    assert response.status_code == 200
    versions = {item["name"]: item["version"] for item in response.json()}
    assert versions["get-sauce"] == "unknown"
    assert all(version.strip() for version in versions.values())


def test_failed_version_probe_never_treats_stderr_as_version(
        client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.main.shutil.which", lambda binary: "/usr/bin/" + binary)
    monkeypatch.setattr("app.main.subprocess.run", lambda argv, **kw:
                        SimpleNamespace(returncode=2, stdout="", stderr="failed to query version"))
    response = client.get("/api/v1/engines", headers=auth_headers)
    assert response.status_code == 200
    assert {engine["version"] for engine in response.json()} == {"unknown"}
