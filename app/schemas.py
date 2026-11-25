from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.engine_registry import ENGINE_NAMES


EngineName = Literal[*ENGINE_NAMES]
EngineSelection = Literal["auto", *ENGINE_NAMES]


class MediaPreviewIn(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    max_entries: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100_000)
    engine: Literal["yt-dlp", "gallery-dl"] = "yt-dlp"

    @model_validator(mode="after")
    def validate_gallery_window(self):
        from app.gallery_selection import MAX_GALLERY_OFFSET
        if self.engine == "gallery-dl" and self.offset > MAX_GALLERY_OFFSET:
            raise ValueError(f"Gallery offset must be at most {MAX_GALLERY_OFFSET}")
        return self



class DirectMetadataIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=4096)


class DirectMetadataOut(BaseModel):
    """Values are null when the server did not supply trusted HEAD headers."""
    model_config = ConfigDict(extra="forbid")
    filename: str | None
    mime: str | None
    size: int | None = Field(ge=0, le=9_007_199_254_740_991)



class TorrentManifestIn(BaseModel):
    """Exactly one source: magnet link or base64-encoded .torrent file."""
    url: str | None = Field(default=None, min_length=8, max_length=4096)
    torrent_base64: str | None = Field(default=None, min_length=4, max_length=2_800_000)

    @model_validator(mode="after")
    def exactly_one_source(self):
        if (self.url is None) == (self.torrent_base64 is None):
            raise ValueError("Provide exactly one of url (magnet) or torrent_base64")
        return self





class PreviewMetadataOut(BaseModel):
    """Explicit nullable extractor metadata; absent values remain null."""
    model_config = ConfigDict(extra="forbid")

    thumbnail: str | None
    duration: float | int | None
    timestamp: float | int | None
    release_timestamp: float | int | None
    upload_date: str | None
    live_status: str | None
    is_live: bool | None


class PreviewEntryOut(PreviewMetadataOut):
    index: int = Field(ge=1)
    id: str
    title: str
    # Optional extensions accepted before upstream flat-playlist metadata is
    # available. Do not invent bytes when the extractor omits these fields.
    size_bytes: int | None = Field(default=None, ge=0)
    size_estimate_bytes: int | None = Field(default=None, ge=0)


class PreviewFormatOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_id: str
    ext: str
    resolution: str
    vcodec: str
    acodec: str
    filesize: int | None


class MediaPreviewOut(PreviewMetadataOut):
    """Success contract shared by Core UI and the Nextcloud plugin."""
    title: str
    id: str
    extractor: str
    kind: Literal["playlist", "video"]
    entry_count: int | None = Field(ge=0)
    entries: list[PreviewEntryOut]
    formats: list[PreviewFormatOut]
    subtitle_languages: list[str]
    offset: int = Field(ge=0)
    max_entries: int = Field(ge=1, le=100)
    ordering: Literal["source"]
    has_more: bool
    next_offset: int | None = Field(ge=0)
    # Some extractors provide size estimates at the preview root, not per entry.
    size_bytes: int | None = Field(default=None, ge=0)
    size_estimate_bytes: int | None = Field(default=None, ge=0)


class TorrentManifestFileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    path: str
    name: str
    size_bytes: int = Field(ge=0)


class TorrentManifestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_count: int = Field(ge=0)
    files: list[TorrentManifestFileOut]

    @model_validator(mode="after")
    def count_matches_entries(self):
        if self.file_count != len(self.files):
            raise ValueError("Torrent manifest file_count must match files")
        return self



class JobCreate(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    engine: EngineSelection = "auto"
    preset_id: str | None = Field(default=None, max_length=36)
    overrides: dict[str, Any] = Field(default_factory=dict)
    owner_id: str | None = Field(default=None, min_length=1, max_length=255, pattern=r"^[^/\\\\\x00-\x1f]+$")
    # Optional signed-gateway retry identity, never included in JobOut.
    submission_id: str | None = Field(
        default=None, min_length=36, max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )
    # One-off destination choice is constrained to one folder below the
    # administrator-configured target; it cannot set a URL or credentials.
    delivery_subfolder: str | None = Field(default=None, max_length=100)
    # Trusted server-side gateway input only. Never included in JobOut.
    delivery: dict[str, Any] | None = None

    @field_validator("delivery_subfolder")
    @classmethod
    def validate_oneoff_subfolder(cls, name: str | None) -> str | None:
        if name is None:
            return None
        value = name.strip()
        if not value:
            return None
        if value in {".", ".."} or "/" in value or "\\" in value or any(ord(char) < 32 for char in value):
            raise ValueError("Choose one folder name, without slashes or parent paths")
        return value


class JobBatchCreate(BaseModel):
    items: list[JobCreate] = Field(min_length=1, max_length=50)


class PresetInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    engine: EngineName
    settings: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", max_length=1000)
    is_shared: bool = Field(default=False, strict=True)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Preset name cannot be blank")
        return value


class EngineRuleInput(BaseModel):
    host_suffix: str = Field(min_length=3, max_length=255)
    path_prefix: str = Field(default="/", max_length=1024)
    engine: EngineName
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True
    description: str = Field(default="", max_length=255)

    @field_validator("host_suffix")
    @classmethod
    def normalize_host_suffix(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if "://" in value or "/" in value or ":" in value or "*" in value or not value:
            raise ValueError("Use a hostname suffix only, without scheme, path, port, or wildcard")
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Invalid internationalized hostname") from exc
        labels = value.split(".")
        if any(not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in labels):
            raise ValueError("Invalid hostname suffix")
        if len(value) > 255:
            raise ValueError("Hostname suffix is too long")
        return value

    @field_validator("path_prefix")
    @classmethod
    def normalize_path_prefix(cls, value: str) -> str:
        value = value.strip() or "/"
        if not value.startswith("/"):
            value = "/" + value
        if "\\" in value or "\x00" in value or "?" in value or "#" in value or ".." in value.split("/"):
            raise ValueError("Path prefix must be a safe URL path without query, fragment, or parent segments")
        return value


class EngineRuleOrderInput(BaseModel):
    """Complete ordering snapshot; no partial priority updates."""
    ids: list[str] = Field(min_length=1, max_length=1001)

    @field_validator("ids")
    @classmethod
    def unique_rule_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Rule IDs must be unique")
        return values


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    url: str
    owner_id: str | None = None
    delivery_subfolder: str | None = None
    engine: str
    status: str
    pause_capability: dict[str, Any]
    overrides: dict[str, Any]
    progress: dict[str, Any]
    output_files: list[dict[str, Any]]
    log_text: str
    error: str | None
    cancel_requested: bool
    stop_requested: bool = False
    can_stop: bool = False
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class JobBatchOut(BaseModel):
    created: int
    jobs: list[JobOut]


class EnginePolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


class EnginePolicyOut(EnginePolicyIn):
    engine: EngineName


class EngineStatusOut(BaseModel):
    name: EngineName
    label: str
    role: str
    priority: str
    available: bool
    enabled: bool = True
    installed: bool
    version: str | None
    defaults: dict[str, Any]
    url_schemes: list[str]


class EngineOptionFieldOut(BaseModel):
    key: str
    label: str
    category: str
    type: str
    description: str
    choices: list[str] | None = None
    minimum: int | None = None
    maximum: int | None = None


class EngineOptionsOut(BaseModel):
    engine: EngineName
    fields: list[EngineOptionFieldOut]


class EngineSettingsOut(BaseModel):
    engine: EngineName
    settings: dict[str, Any]


class CookieFileOut(BaseModel):
    filename: str
    size_bytes: int = Field(ge=0)


class CookieUploadedOut(CookieFileOut):
    stored: bool


class CookieDeletedOut(BaseModel):
    deleted: bool
    filename: str


class DeliverySettingsOut(BaseModel):
    mode: Literal["local", "webdav"]
    folder: str
    password_configured: bool
    webdav_url: str | None = None
    username: str | None = None


class DeliveryTestOut(BaseModel):
    mode: Literal["local", "webdav"]
    verified: bool
    cleaned_up: bool
    message: str


class EngineSettingsIn(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


class DeliverySettingsIn(BaseModel):
    mode: Literal["local", "webdav"] = "local"
    webdav_url: str | None = Field(default=None, max_length=2048)
    username: str | None = Field(default=None, max_length=255)
    app_password: str | None = Field(default=None, max_length=1024)
    folder: str = Field(default="Downloader", max_length=512)

    @field_validator("folder")
    @classmethod
    def safe_folder(cls, value: str) -> str:
        value = value.strip().strip("/")
        if (value and any(segment in {"", ".", ".."} for segment in value.split("/"))) or "\\" in value or "\x00" in value:
            raise ValueError("Delivery folder must contain nonempty path segments without dot traversal")
        return value

    @field_validator("webdav_url")
    @classmethod
    def validate_webdav_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("webdav_url must be an absolute HTTP(S) URL without credentials, query, or fragment")
        return value

    @model_validator(mode="after")
    def require_webdav_fields(self):
        if self.mode == "webdav" and (not self.webdav_url or not self.username):
            raise ValueError("webdav_url and username are required for WebDAV delivery")
        return self


class ProvidedCookieOut(BaseModel):
    hostname: str
    stored: bool


class ProvidedCookieDeletedOut(BaseModel):
    hostname: str
    deleted: bool


class CookieUploadIn(BaseModel):
    content_base64: str = Field(min_length=4, max_length=1_400_000)
