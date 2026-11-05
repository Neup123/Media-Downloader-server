from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_token: str
    data_dir: Path
    output_dir: Path
    max_concurrent_downloads: int
    aria2_rpc_port: int
    encryption_key: str | None
    allow_private_targets: bool
    allow_private_webdav_targets: bool = False
    allow_insecure_webdav: bool = False
    max_concurrent_per_engine: dict[str, int] = field(
        default_factory=lambda: {"yt-dlp": 1, "aria2c": 1, "gallery-dl": 1, "generic": 1, "you-get": 1, "svtplay-dl": 1, "get-sauce": 1}
    )

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("DATA_DIR", "/data")).resolve()
        output_dir = Path(os.getenv("OUTPUT_DIR", str(data_dir / "downloads"))).resolve()
        return cls(
            database_url=os.getenv("DATABASE_URL", f"sqlite:///{data_dir / 'downloader.db'}"),
            api_token=os.getenv("APP_API_TOKEN", ""),
            data_dir=data_dir,
            output_dir=output_dir,
            max_concurrent_downloads=max(1, min(8, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "2")))),
            aria2_rpc_port=int(os.getenv("ARIA2_RPC_PORT", "16800")),
            encryption_key=os.getenv("APP_ENCRYPTION_KEY") or None,
            allow_private_targets=os.getenv("ALLOW_PRIVATE_TARGETS", "false").lower() == "true",
            allow_private_webdav_targets=os.getenv("ALLOW_PRIVATE_WEBDAV_TARGETS", "false").lower() == "true",
            allow_insecure_webdav=os.getenv("ALLOW_INSECURE_WEBDAV", "false").lower() == "true",
            max_concurrent_per_engine={
                "yt-dlp": max(1, min(8, int(os.getenv("MAX_CONCURRENT_YTDLP", "1")))),
                "aria2c": max(1, min(8, int(os.getenv("MAX_CONCURRENT_ARIA2", "1")))),
                "gallery-dl": max(1, min(8, int(os.getenv("MAX_CONCURRENT_GALLERY_DL", "1")))),
                "generic": max(1, min(8, int(os.getenv("MAX_CONCURRENT_GENERIC", "1")))),
                "you-get": max(1, min(8, int(os.getenv("MAX_CONCURRENT_YOU_GET", "1")))),
                "svtplay-dl": max(1, min(8, int(os.getenv("MAX_CONCURRENT_SVTPLAY_DL", "1")))),
                "get-sauce": max(1, min(8, int(os.getenv("MAX_CONCURRENT_GET_SAUCE", "1")))),
            },
        )

    def prepare_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "cookies").mkdir(parents=True, exist_ok=True)
