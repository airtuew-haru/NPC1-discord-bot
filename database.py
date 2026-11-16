from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import aiosqlite

DEFAULT_SETTINGS: dict[str, Any] = {
    "welcome_enabled": 0,
    "welcome_channel_id": None,
    "welcome_template": "Welcome <id> to %guild%! You are member #%member_count%.",
    "welcome_image_enabled": 0,
    "welcome_image_url": None,
    "avatar_position": "center",
    "ai_channel_id": None,
    "ai_prompt": "You are a friendly and helpful Discord assistant. Reply naturally and concisely.",
    "autorole_enabled": 0,
    "autorole_role_id": None,
}

ALLOWED_SETTINGS = frozenset(DEFAULT_SETTINGS)


class Database:
    """PostgreSQL for hosted deployments; SQLite for local development."""

    def __init__(self, path: str | None = None, database_url: str | None = None) -> None:
        raw_database_url = os.getenv("DATABASE_URL", "") if database_url is None else database_url
        self.database_url = raw_database_url.strip() or None
        self.path = path or os.getenv("DB_PATH", "data/bot.sqlite3")
        self.pool: Any | None = None

    @property
    def backend(self) -> str:
        return "postgresql" if self.database_url else "sqlite"

    async def initialize(self) -> None:
        if self.database_url:
            await self._initialize_postgres()
        else:
            await self._initialize_sqlite()

    async def _initialize_postgres(self) -> None:
        try:
            import asyncpg
        except ImportError as exc:  # pragma: no cover - dependency installation issue
            raise RuntimeError("asyncpg is required when DATABASE_URL is configured.") from exc

        # Supabase's shared pooler is IPv4-compatible. Disabling the prepared
        # statement cache also keeps this driver compatible with transaction poolers.
        self.pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=1,
            max_size=4,
            ssl="require",
            statement_cache_size=0,
            command_timeout=30,
        )
        async with self.pool.acquire() as conn:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    welcome_enabled SMALLINT NOT NULL DEFAULT 0,
                    welcome_channel_id BIGINT,
                    welcome_template TEXT NOT NULL DEFAULT 'Welcome <id> to %guild%! You are member #%member_count%.',
                    welcome_image_enabled SMALLINT NOT NULL DEFAULT 0,
                    welcome_image_url TEXT,
                    avatar_position TEXT NOT NULL DEFAULT 'center',
                    ai_channel_id BIGINT,
                    ai_prompt TEXT NOT NULL DEFAULT 'You are a friendly and helpful Discord assistant. Reply naturally and concisely.',
                    autorole_enabled SMALLINT NOT NULL DEFAULT 0,
                    autorole_role_id BIGINT
                )"""
            )
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS ai_history (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_history_channel ON ai_history(guild_id, channel_id, id)"
            )

    async def _initialize_sqlite(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    welcome_enabled INTEGER NOT NULL DEFAULT 0,
                    welcome_channel_id INTEGER,
                    welcome_template TEXT NOT NULL DEFAULT 'Welcome <id> to %guild%! You are member #%member_count%.',
                    welcome_image_enabled INTEGER NOT NULL DEFAULT 0,
                    welcome_image_url TEXT,
                    avatar_position TEXT NOT NULL DEFAULT 'center',
                    ai_channel_id INTEGER,
                    ai_prompt TEXT NOT NULL DEFAULT 'You are a friendly and helpful Discord assistant. Reply naturally and concisely.',
                    autorole_enabled INTEGER NOT NULL DEFAULT 0,
                    autorole_role_id INTEGER
                )"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS ai_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_history_channel ON ai_history(guild_id, channel_id, id)"
            )
            await db.commit()

    async def get_guild_settings(self, guild_id: int) -> dict[str, Any]:
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM guild_settings WHERE guild_id = $1", guild_id
                )
            settings = dict(DEFAULT_SETTINGS)
            if row is not None:
                settings.update(dict(row))
            return settings

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
            )
            row = await cursor.fetchone()
        settings = dict(DEFAULT_SETTINGS)
        if row is not None:
            settings.update(dict(row))
        return settings

    async def update_guild_settings(self, guild_id: int, **values: Any) -> None:
        if not values:
            return
        unknown = set(values) - ALLOWED_SETTINGS
        if unknown:
            raise ValueError(f"Unsupported settings: {', '.join(sorted(unknown))}")

        keys = list(values)
        columns = ["guild_id", *keys]
        params = [guild_id, *(values[key] for key in keys)]

        if self.pool is not None:
            placeholders = ", ".join(f"${index}" for index in range(1, len(params) + 1))
            updates = ", ".join(f"{key} = EXCLUDED.{key}" for key in keys)
            query = (
                f"INSERT INTO guild_settings ({', '.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT(guild_id) DO UPDATE SET {updates}"
            )
            async with self.pool.acquire() as conn:
                await conn.execute(query, *params)
            return

        placeholders = ", ".join("?" for _ in params)
        updates = ", ".join(f"{key} = excluded.{key}" for key in keys)
        query = (
            f"INSERT INTO guild_settings ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(guild_id) DO UPDATE SET {updates}"
        )
        async with aiosqlite.connect(self.path) as db:
            await db.execute(query, params)
            await db.commit()

    async def get_ai_history(
        self, guild_id: int, channel_id: int, limit: int = 12
    ) -> list[dict[str, str]]:
        safe_limit = max(1, min(int(limit), 100))
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT role, content FROM ai_history
                       WHERE guild_id = $1 AND channel_id = $2
                       ORDER BY id DESC LIMIT $3""",
                    guild_id,
                    channel_id,
                    safe_limit,
                )
            return [dict(row) for row in reversed(rows)]

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT role, content FROM ai_history
                   WHERE guild_id = ? AND channel_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (guild_id, channel_id, safe_limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in reversed(rows)]

    async def add_ai_history(
        self, guild_id: int, channel_id: int, role: str, content: str, keep: int = 24
    ) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("Invalid AI history role")
        safe_keep = max(2, min(int(keep), 200))
        content = content[:6000]

        if self.pool is not None:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO ai_history (guild_id, channel_id, role, content) VALUES ($1, $2, $3, $4)",
                        guild_id,
                        channel_id,
                        role,
                        content,
                    )
                    await conn.execute(
                        """DELETE FROM ai_history WHERE guild_id = $1 AND channel_id = $2
                           AND id NOT IN (
                               SELECT id FROM ai_history WHERE guild_id = $1 AND channel_id = $2
                               ORDER BY id DESC LIMIT $3
                           )""",
                        guild_id,
                        channel_id,
                        safe_keep,
                    )
            return

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO ai_history (guild_id, channel_id, role, content) VALUES (?, ?, ?, ?)",
                (guild_id, channel_id, role, content),
            )
            await db.execute(
                """DELETE FROM ai_history WHERE guild_id = ? AND channel_id = ?
                   AND id NOT IN (
                       SELECT id FROM ai_history WHERE guild_id = ? AND channel_id = ?
                       ORDER BY id DESC LIMIT ?
                   )""",
                (guild_id, channel_id, guild_id, channel_id, safe_keep),
            )
            await db.commit()

    async def clear_ai_history(self, guild_id: int, channel_id: int) -> None:
        if self.pool is not None:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM ai_history WHERE guild_id = $1 AND channel_id = $2",
                    guild_id,
                    channel_id,
                )
            return

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "DELETE FROM ai_history WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )
            await db.commit()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
