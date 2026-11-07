from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # Trusted gateway-only fields. The encrypted token is NEVER included in JobOut.
    owner_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    delivery_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_subfolder: Mapped[str | None] = mapped_column(String(100), nullable=True)
    engine: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    log_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EngineSetting(Base):
    __tablename__ = "engine_settings"

    engine: Mapped[str] = mapped_column(String(40), primary_key=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Preset(Base):
    __tablename__ = "presets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class EngineRule(Base):
    __tablename__ = "engine_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    host_suffix: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    path_prefix: Mapped[str] = mapped_column(String(1024), nullable=False, default="/")
    engine: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class DeliverySetting(Base):
    __tablename__ = "delivery_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    webdav_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    folder: Mapped[str] = mapped_column(Text, nullable=False, default="Downloader")


def make_engine(database_url: str):
    if database_url.startswith("sqlite:///"):
        db_path = database_url.removeprefix("sqlite:///")
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
        )
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        return engine
    return create_engine(database_url, pool_pre_ping=True)


def initialize_database(engine) -> None:
    Base.metadata.create_all(engine)
    # Additive migration of existing pre-Nextcloud standalone databases.
    # Never drop or recreate the jobs table: users may have active downloads.
    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    preset_columns = {column["name"] for column in inspect(engine).get_columns("presets")}
    with engine.begin() as connection:
        if "owner_id" not in columns:
            connection.execute(text("ALTER TABLE jobs ADD COLUMN owner_id VARCHAR(255)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_owner_id ON jobs (owner_id)"))
        if "delivery_token" not in columns:
            connection.execute(text("ALTER TABLE jobs ADD COLUMN delivery_token TEXT"))
        if "delivery_subfolder" not in columns:
            connection.execute(text(
                "ALTER TABLE jobs ADD COLUMN delivery_subfolder VARCHAR(100)"
            ))
        # Existing presets were private to the standalone administrator; they
        # must never become visible to Nextcloud users without an explicit grant.
        if "is_shared" not in preset_columns:
            connection.execute(text(
                "ALTER TABLE presets ADD COLUMN is_shared BOOLEAN NOT NULL DEFAULT FALSE"
            ))


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def append_log(job: Job, message: str, max_chars: int = 100_000) -> None:
    stamp = utcnow().isoformat(timespec="seconds")
    job.log_text = (job.log_text + f"[{stamp}] {message}\n")[-max_chars:]
    job.updated_at = utcnow()


def get_queued_ids(session: Session, limit: int) -> list[str]:
    rows = session.scalars(select(Job.id).where(Job.status == "queued", Job.cancel_requested.is_(False)).order_by(Job.created_at).limit(limit)).all()
    return list(rows)


def get_queued_jobs(session: Session, limit: int, excluded_engines: set[str] | None = None) -> list[Job]:
    query = select(Job).where(Job.status == "queued", Job.cancel_requested.is_(False))
    if excluded_engines:
        query = query.where(Job.engine.notin_(excluded_engines))
    return list(session.scalars(query.order_by(Job.created_at).limit(limit)).all())
