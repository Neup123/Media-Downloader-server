"""Typed, self-describing common engine option fields for the web UI.

Field names and their semantics follow the engines' current upstream manuals.
Unknown or extractor-specific settings still use the advanced editor.
"""

from __future__ import annotations

from typing import Any


def field(key: str, label: str, category: str, kind: str = "text", description: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "key": key, "label": label, "category": category,
        "type": kind, "description": description, **extra,
    }


OPTION_CATALOG: dict[str, list[dict[str, Any]]] = {
    "get-sauce": [
        field("stream_index", "Stream/quality index", "Media", "integer", "0 selects the best stream", minimum=0, maximum=10000),
        field("amount", "Limit image downloads", "Media", "integer", "0 uses engine default", minimum=0, maximum=100000),
        field("pages", "Page indices/ranges", "Media", "text", "Comma-separated indices and ranges"),
        field("workers", "Download workers", "Concurrency", "integer", minimum=1, maximum=32),
        field("timeout_minutes", "HTTP timeout (minutes)", "Network", "integer", minimum=1, maximum=120),
        field("merge", "Output merge mode", "Files", "text", "default, none or cbz"),
        field("subdirectory", "Create media subfolder", "Files", "boolean"),
    ],
    "svtplay-dl": [
        field("quality", "Quality selector", "Media", "text", "svtplay-dl --quality"),
        field("resolution", "Preferred resolutions", "Media", "text", "Comma-separated values such as 480,720,1080"),
        field("output_format", "Output container (mp4 or mkv)", "Files", "text"),
        field("subtitles", "Download subtitles", "Subtitles", "boolean"),
        field("all_subtitles", "Download all subtitle languages", "Subtitles", "boolean"),
        field("all_episodes", "Download all episodes", "Series", "boolean"),
    ],
    "you-get": [
        field("format", "Format code", "Media", "text", "you-get --format, e.g. stream identifier"),
        field("no_proxy", "Disable system proxy", "Network", "boolean"),
        field("cookies_file", "Cookie file", "Authentication", "text", "Netscape cookie file stored by the service"),
    ],
    "generic": [
        field("filename", "Output filename", "File", "text", "Simple filename, leave blank to derive from URL"),
        field("follow_redirects", "Follow HTTP redirects", "Network", "boolean", "Disabled by default until trusted egress controls are configured"),
        field("max_redirects", "Maximum redirects", "Network", "integer", minimum=0, maximum=10),
        field("retry_count", "Retry attempts", "Network", "integer", minimum=0, maximum=10),
        field("user_agent", "User-Agent header", "HTTP", "text"),
    ],
    "yt-dlp": [
        field("format", "Format selector", "Quality", "text", "yt-dlp -f selector, e.g. bv*+ba/b"),
        field("allow_playlist", "Allow playlists", "Playlists", "boolean", "Enable playlist downloads"),
        field("playlist_items", "Playlist items", "Playlists", "text", "Comma-separated indices and ranges"),
        field("subtitles_lang", "Subtitle languages", "Subtitles", "text"),
        field("write_subtitles", "Download subtitles", "Subtitles", "boolean"),
        field("embed_subtitles", "Embed subtitles", "Subtitles", "boolean"),
        field("cookies_file", "Cookies file", "Authentication", "text"),
    ],
    "aria2c": [
        field("max-connection-per-server", "Connections per server", "Connections", "integer", minimum=1, maximum=16),
        field("split", "Split download into parts", "Connections", "integer", minimum=1, maximum=64),
        field("min-split-size", "Minimum split size", "Connections", "text", "e.g. 1M"),
        field("max-download-limit", "Download speed limit", "Bandwidth", "text", "e.g. 2M"),
        field("lowest-speed-limit", "Minimum accepted speed", "Bandwidth", "text", "e.g. 4K"),
        field("connect-timeout", "Connect timeout (seconds)", "Network", "integer", minimum=1, maximum=600),
        field("timeout", "Network timeout (seconds)", "Network", "integer", minimum=1, maximum=3600),
        field("max-tries", "Retry attempts", "Retries", "integer", minimum=0, maximum=100),
        field("retry-wait", "Retry wait (seconds)", "Retries", "integer", minimum=0, maximum=3600),
        field("user-agent", "User-Agent", "HTTP", "text"),
        field("referer", "Referer", "HTTP", "text"),
        field("header", "Custom HTTP header", "HTTP", "text", "Do not enter private credentials in shared settings"),
        field("allow-overwrite", "Overwrite existing files", "Files", "boolean"),
        field("auto-file-renaming", "Rename conflicting files", "Files", "boolean"),
        field("check-integrity", "Verify integrity", "Files", "boolean"),
        field("seed-time", "Torrent seed duration (minutes)", "BitTorrent", "integer", minimum=0, maximum=100000),
        field("seed-ratio", "Torrent seed ratio", "BitTorrent", "text"),
        field("bt-max-peers", "Maximum torrent peers", "BitTorrent", "integer", minimum=0, maximum=10000),
        field("bt-stop-timeout", "Stop torrent after idle (seconds)", "BitTorrent", "integer", minimum=0, maximum=86400),
    ],
    # Credentials, policy and daemon settings are separate protected APIs,
    # not ordinary per-job option fields.
    "telegram": [],
    "pyload": [],
    "musicdl": [
        field("source", "Music source client", "Music",
              "text", "Optional: leave blank to detect a supported client from the link"),
    ],
    "ddddocr": [],
    "gallery-dl": [
        field("extractor.sleep", "Delay before each download", "Rate limits", "text", "Seconds or duration range"),
        field("extractor.sleep-request", "Delay between extractor requests", "Rate limits", "text", "Seconds or duration range"),
        field("extractor.sleep-retries", "Retry backoff", "Retries", "text", "e.g. lin=1 or exp=30"),
        field("extractor.skip", "Skip existing files", "Files", "boolean"),
        field("extractor.filename", "Output filename template", "Files", "text", "Use extractor metadata keys"),
        field("extractor.directory", "Album subdirectory template", "Files", "text", "Relative to service-controlled base"),
        field("extractor.file-unique", "Skip duplicate URLs", "Files", "boolean"),
        field("extractor.date-after", "Only items after date", "Filtering", "text", "ISO 8601 date or timestamp"),
        field("extractor.date-before", "Only items before date", "Filtering", "text", "ISO 8601 date or timestamp"),
    ],
}


def get_catalog(engine: str) -> list[dict[str, Any]]:
    if engine not in OPTION_CATALOG:
        raise ValueError("Unsupported engine")
    return OPTION_CATALOG[engine]
