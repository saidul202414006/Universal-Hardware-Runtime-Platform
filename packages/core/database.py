"""
Database Layer — SQLAlchemy 2.0 async with SQLite (default) / PostgreSQL (optional).

Tables:
- devices: Hardware device registry (persistent across restarts)
- tasks: Task history and current tasks
- events: Persistent event log (for CRITICAL events and audit)
- plugins: Loaded plugin registry
- adapters: Registered adapter registry
- runtime_state: Key-value store for runtime state persistence

All operations are async using aiosqlite (SQLite) or asyncpg (PostgreSQL).
Alembic handles schema migrations.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, AsyncIterator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from packages.logger import get_logger

logger = get_logger("database")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =============================================================================
# ORM Base
# =============================================================================


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""
    pass


# =============================================================================
# ORM Models
# =============================================================================


class DeviceRecord(Base):
    """Persistent device registry — survives runtime restarts."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    device_type: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="offline")
    port: Mapped[str] = mapped_column(String(255), nullable=False)
    vid: Mapped[str | None] = mapped_column(String(10), nullable=True)
    pid: Mapped[str | None] = mapped_column(String(10), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adapter_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plugin_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    info: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON dict
    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    last_state_change: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class TaskRecord(Base):
    """Task history — all tasks, current and historical."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plugin_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adapter_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    progress_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    params: Mapped[str] = mapped_column(Text, nullable=False, default="{}")     # JSON
    result: Mapped[str | None] = mapped_column(Text, nullable=True)             # JSON
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_lines: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EventRecord(Base):
    """Persistent event log — CRITICAL events and audit trail."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")    # JSON


class PluginRecord(Base):
    """Plugin registry — tracks loaded plugins and their state."""

    __tablename__ = "plugins"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="unloaded")
    manifest: Mapped[str] = mapped_column(Text, nullable=False, default="{}")   # JSON
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AdapterRecord(Base):
    """Adapter registry — tracks registered adapters."""

    __tablename__ = "adapters"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_health_check: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RuntimeStateRecord(Base):
    """Key-value store for runtime state persistence."""

    __tablename__ = "runtime_state"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


# =============================================================================
# Database Manager
# =============================================================================


class DatabaseManager:
    """
    Manages the async database connection and session lifecycle.

    Usage:
        db = DatabaseManager("sqlite+aiosqlite:///./data/runtime.db")
        await db.initialize()    # Creates tables, runs migrations

        async with db.session() as session:
            # Use session for CRUD
            pass

        await db.close()
    """

    def __init__(self, database_url: str, echo: bool = False) -> None:
        self._url = database_url
        self._echo = echo
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def initialize(self) -> None:
        """Create engine, session factory, and all tables."""
        import os
        from pathlib import Path

        # Ensure data directory exists for SQLite
        if "sqlite" in self._url:
            # Extract path from URL
            db_path = self._url.replace("sqlite+aiosqlite:///", "")
            db_dir = Path(db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)

        self._engine = create_async_engine(
            self._url,
            echo=self._echo,
            # Connection pool settings (SQLite doesn't use pool in the same way)
            pool_pre_ping=True,
        )

        # Enable WAL mode for SQLite (better concurrent read performance)
        if "sqlite" in self._url:
            @event.listens_for(self._engine.sync_engine, "connect")
            def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:  # noqa: ARG001
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )

        # Create all tables (idempotent — skips existing tables)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Database initialized", url=self._url.split("///")[0])

    async def close(self) -> None:
        """Close the database engine and all connections."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            logger.info("Database connection closed")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """
        Async context manager that provides a database session.

        Usage:
            async with db.session() as session:
                result = await session.execute(...)
        """
        if self._session_factory is None:
            raise RuntimeError("Database not initialized. Call await db.initialize() first.")

        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def health_check(self) -> bool:
        """Verify the database is reachable and functional."""
        try:
            async with self.session() as session:
                await session.execute(
                    __import__("sqlalchemy").text("SELECT 1")
                )
            return True
        except Exception as exc:
            logger.error("Database health check failed", error=str(exc))
            return False


# Module-level singleton
_db: DatabaseManager | None = None


def get_database() -> DatabaseManager:
    """Get the global DatabaseManager singleton."""
    global _db
    if _db is None:
        raise RuntimeError(
            "Database not initialized. Call initialize_database() at startup."
        )
    return _db


def initialize_database(url: str, echo: bool = False) -> DatabaseManager:
    """Create and return the global DatabaseManager singleton."""
    global _db
    _db = DatabaseManager(url, echo=echo)
    return _db
