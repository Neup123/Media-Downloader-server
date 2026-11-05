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
