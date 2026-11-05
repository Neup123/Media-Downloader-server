from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EngineName = Literal["yt-dlp", "aria2c"]


class JobCreate(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    engine: EngineName = "yt-dlp"
    overrides: dict[str, Any] = Field(default_factory=dict)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    url: str
    engine: str
    status: str
    overrides: dict[str, Any]
    progress: dict[str, Any]
    output_files: list[dict[str, Any]]
    error: str | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


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
        if ".." in value.split("/") or "\\" in value or "\x00" in value:
            raise ValueError("Delivery folder must be a relative path without parent segments")
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


class CookieUploadIn(BaseModel):
    content_base64: str = Field(min_length=4, max_length=1_400_000)
