from app.adapters.gallery_dl import parse_progress_line as parse_gallery_progress_line
from app.adapters.yt_dlp import parse_progress_line
from app.security import UnsafeUrlError, validate_extra_args, validate_target_url


def test_ytdlp_progress_parsing():
    event = parse_progress_line("download:__DL_PROGRESS__1024\t4096\tNA\t512\t6\tclip.mp4")
    assert event is not None
    assert event["downloaded_bytes"] == 1024
    assert event["total_bytes"] == 4096
    assert event["percent"] == 25.0
    assert event["speed_bytes_per_second"] == 512
    assert event["eta_seconds"] == 6
    assert event["filename"] == "clip.mp4"


def test_unknown_total_is_not_fabricated():
    event = parse_progress_line("download:__DL_PROGRESS__1024\tNA\tNA\tNA\tNA\tclip.mp4")
    assert event is not None
    assert event["total_bytes"] is None
    assert event["percent"] is None


def test_extra_args_reject_service_managed_flags():
    assert validate_extra_args(["--no-warnings"]) == ["--no-warnings"]
    try:
        validate_extra_args(["--output=/tmp/out"])
    except ValueError as exc:
        assert "managed" in str(exc)
    else:
        raise AssertionError("service-managed output flag should be rejected")


def test_unsafe_schemes_and_hosts_are_rejected():
    for value in ["file:///etc/passwd", "http://127.0.0.1/", "http://10.0.0.5/", "http://service.local/"]:
        try:
            validate_target_url(value, "yt-dlp")
        except UnsafeUrlError:
            continue
        raise AssertionError(f"unsafe URL accepted: {value}")


def test_magnet_is_aria2_only():
    magnet = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    assert validate_target_url(magnet, "aria2c") == magnet
    try:
        validate_target_url(magnet, "yt-dlp")
    except UnsafeUrlError:
        pass
    else:
        raise AssertionError("yt-dlp should not accept magnet URIs in this API")


def test_gallery_dl_progress_parsing_does_not_invent_total_bytes():
    event = parse_gallery_progress_line("16%   1.01MB 337.19kB/s")
    assert event is not None
    assert event["percent"] == 16.0
    assert event["downloaded_bytes"] == 1_010_000
    assert event["speed_bytes_per_second"] == 337_190
    assert event["total_bytes"] is None
