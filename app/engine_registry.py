"""Canonical downloader engine registry: identity, defaults, options and UI metadata.

The standalone Core UI generates its engine IDs, descriptions and default values
from this module. Nextcloud's native plugin owns only its own presentation, not a
second standalone UI or engine registry.
"""
from __future__ import annotations

from typing import Any
from app.adapters.get_sauce import DEFAULTS as GET_SAUCE_DEFAULTS

def field(key: str, label: str, category: str, kind: str = "text", description: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "key": key, "label": label, "category": category,
        "type": kind, "description": description, **extra,
    }


ENGINE_REGISTRY: dict[str, dict[str, Any]] = {
    "yt-dlp": {
        # Per-user cookie selection is valid but must not be an administrator
        # default. Keep its allowed setting name here, not in the API adapter.
        "additional_settings": ("cookies_file",),
        "label": "yt-dlp",
        "role": "Site extraction and media downloads",
        "priority": "P0",
        "available": True,
        "title": "Video & audio sites",
        "purpose": "Videos, playlists, subtitles and audio from supported sites",
        "defaults": {
        "format": "bv*+ba/b",
        "subtitles_lang": "",
        "write_subtitles": False,
        "embed_subtitles": False,
        "extra_args": [],
        "allow_playlist": False,
        "playlist_items": "",
    },
        "fields": [
        field("format", "Format selector", "Quality", "text", "yt-dlp -f selector, e.g. bv*+ba/b"),
        field("allow_playlist", "Allow playlists", "Playlists", "boolean", "Enable playlist downloads"),
        field("playlist_items", "Playlist items", "Playlists", "text", "Comma-separated indices and ranges"),
        field("subtitles_lang", "Subtitle languages", "Subtitles", "text"),
        field("write_subtitles", "Download subtitles", "Subtitles", "boolean"),
        field("embed_subtitles", "Embed subtitles", "Subtitles", "boolean"),
        field("cookies_file", "Cookies file", "Authentication", "text"),
    ],
    },
    "aria2c": {
        "label": "aria2c",
        "role": "Direct downloads, BitTorrent, and magnet links",
        "priority": "P0",
        "available": True,
        "title": "Large files & torrents",
        "purpose": "Direct files, magnets, FTP and torrent downloads",
        "defaults": {
        "options": {
            "max-connection-per-server": "4",
            "split": "4",
            "max-tries": "5",
            "retry-wait": "5",
        },
    },
        "fields": [
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
        field("bt-max-peers", "Maximum torrent peers", "BitTorrent", "integer", minimum=0, maximum=10000),
        field("bt-stop-timeout", "Stop torrent after idle (seconds)", "BitTorrent", "integer", minimum=0, maximum=86400),
    ],
    },
    "gallery-dl": {
        "label": "gallery-dl",
        "role": "Image galleries and supported image-board extractors",
        "priority": "P0",
        "available": True,
        "title": "Image galleries",
        "purpose": "Image collections from supported gallery websites",
        "defaults": {
        "options": {},
        "cookies_file": "",
        "proxy": "",
    },
        "fields": [
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
    },
    "generic": {
        "label": "generic / curl",
        "role": "Plain direct HTTP(S) downloads",
        "priority": "P1",
        "available": True,
        "title": "Direct file downloads",
        "purpose": "Files from public HTTP and HTTPS links",
        "defaults": {"filename": "", "follow_redirects": False, "max_redirects": 5, "retry_count": 2, "user_agent": ""},
        "fields": [
        field("filename", "Output filename", "File", "text", "Simple filename, leave blank to derive from URL"),
        field("follow_redirects", "Follow HTTP redirects", "Network", "boolean", "Disabled by default until trusted egress controls are configured"),
        field("max_redirects", "Maximum redirects", "Network", "integer", minimum=0, maximum=10),
        field("retry_count", "Retry attempts", "Network", "integer", minimum=0, maximum=10),
        field("user_agent", "User-Agent header", "HTTP", "text"),
    ],
    },
    "you-get": {
        "label": "you-get",
        "role": "Fallback site extractor",
        "priority": "P1",
        "available": True,
        "title": "Alternative video sites",
        "purpose": "Fallback downloader for supported videos and audio",
        "defaults": {"format": "", "cookies_file": "", "no_proxy": False},
        "fields": [
        field("format", "Format code", "Media", "text", "you-get --format, e.g. stream identifier"),
        field("no_proxy", "Disable system proxy", "Network", "boolean"),
        field("cookies_file", "Cookie file", "Authentication", "text", "Netscape cookie file stored by the service"),
    ],
    },
    "svtplay-dl": {
        "label": "svtplay-dl",
        "role": "Nordic broadcaster and video services",
        "priority": "P2",
        "available": True,
        "title": "Nordic television",
        "purpose": "Video and subtitles from Nordic broadcasting sites",
        "defaults": {"resolution": "", "quality": "", "subtitles": False, "all_subtitles": False, "all_episodes": False, "output_format": "mp4"},
        "fields": [
        field("quality", "Quality selector", "Media", "text", "svtplay-dl --quality"),
        field("resolution", "Preferred resolutions", "Media", "text", "Comma-separated values such as 480,720,1080"),
        field("output_format", "Output container", "Files", "enum", "Container file format", choices=["mp4", "mkv"]),
        field("subtitles", "Download subtitles", "Subtitles", "boolean"),
        field("all_subtitles", "Download all subtitle languages", "Subtitles", "boolean"),
        field("all_episodes", "Download all episodes", "Series", "boolean"),
    ],
    },
    "get-sauce": {
        "label": "get-sauce",
        "role": "Specialized media and gallery extractor",
        "priority": "P2",
        "available": True,
        "title": "Specialized media",
        "purpose": "Specialized media and gallery download sources",
        "defaults": GET_SAUCE_DEFAULTS,
        "fields": [
        field("stream_index", "Stream/quality index", "Media", "integer", "0 selects the best stream", minimum=0, maximum=10000),
        field("amount", "Limit image downloads", "Media", "integer", "0 uses engine default", minimum=0, maximum=100000),
        field("pages", "Page indices/ranges", "Media", "text", "Comma-separated indices and ranges"),
        field("workers", "Download workers", "Concurrency", "integer", minimum=1, maximum=32),
        field("timeout_minutes", "HTTP timeout (minutes)", "Network", "integer", minimum=1, maximum=120),
        field("merge", "Output merge mode", "Files", "enum", "Choose the packaged output format", choices=["default", "none", "cbz"]),
        field("subdirectory", "Create media subfolder", "Files", "boolean"),
    ],
    },
}

ENGINE_NAMES = tuple(ENGINE_REGISTRY)
ENGINE_SETTING_KEYS: dict[str, frozenset[str]] = {
    name: frozenset(row["defaults"]) | frozenset(row.get("additional_settings", ()))
    for name, row in ENGINE_REGISTRY.items()
}
ENGINE_DEFAULTS: dict[str, dict[str, Any]] = {
    name: row["defaults"] for name, row in ENGINE_REGISTRY.items()
}
ENGINE_DESCRIPTIONS = [
    {"name": name, **{key: row[key] for key in ("label", "role", "priority", "available")}}
    for name, row in ENGINE_REGISTRY.items()
]
OPTION_CATALOG: dict[str, list[dict[str, Any]]] = {
    name: row["fields"] for name, row in ENGINE_REGISTRY.items()
}


def get_catalog(engine: str) -> list[dict[str, Any]]:
    if engine not in ENGINE_REGISTRY:
        raise ValueError("Unsupported engine")
    return ENGINE_REGISTRY[engine]["fields"]
