from __future__ import annotations

import os
from dataclasses import dataclass
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
        )

    def prepare_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "cookies").mkdir(parents=True, exist_ok=True)
