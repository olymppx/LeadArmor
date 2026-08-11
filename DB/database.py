from __future__ import annotations

import logging
from datetime import timedelta

import asyncpg

from config import settings

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'post_type_enum') THEN
        CREATE TYPE post_type_enum AS ENUM ('organic', 'ad');
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'lead_status_enum') THEN
        CREATE TYPE lead_status_enum AS ENUM (
            'new',              -- лид только зафиксирован вебхуком
            'notified',         -- менеджер получил уведомление в Telegram
            'phone_requested',  -- отправлен запрос номера в Директ
            'phone_received',   -- номер получен от клиента
            'closed'            -- лид закрыт (продажа/отказ)
        );
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS clients (
    id                        SERIAL PRIMARY KEY,
    name                      VARCHAR(255) NOT NULL,
    ig_business_id            VARCHAR(64) NOT NULL UNIQUE,
    page_access_token         TEXT NOT NULL,          -- ⚠️ в проде шифровать (pgcrypto / KMS)
    manager_chat_id           BIGINT NOT NULL,
    is_active                 BOOLEAN NOT NULL DEFAULT TRUE,
    trial_starts_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    subscription_expires_at   TIMESTAMPTZ,
    google_sheet_id           VARCHAR(128),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE clients ADD COLUMN IF NOT EXISTS trial_starts_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE clients ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMPTZ;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS google_sheet_id VARCHAR(128);

CREATE TABLE IF NOT EXISTS leads (
    id                  BIGSERIAL PRIMARY KEY,
    client_id           INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    ig_comment_id       VARCHAR(64) NOT NULL UNIQUE,
    ig_media_id         VARCHAR(64) NOT NULL,
    ig_user_id          VARCHAR(64) NOT NULL,
    ig_username         VARCHAR(255),
    comment_text        TEXT NOT NULL,
    post_type           post_type_enum NOT NULL,
    status              lead_status_enum NOT NULL DEFAULT 'new',
    phone_number        VARCHAR(32),
    is_comment_removed  BOOLEAN NOT NULL DEFAULT FALSE,  -- актуально только для post_type = 'ad'
    sheet_row           INTEGER,                          -- номер строки в Google Sheets этого лида
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE leads ADD COLUMN IF NOT EXISTS sheet_row INTEGER;

CREATE INDEX IF NOT EXISTS idx_leads_ig_user_id ON leads(ig_user_id);
"""


class Database:


    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=str(settings.dsn),
            min_size=2,
            max_size=10,
        )
        logger.info(
            "PostgreSQL: пул соединений создан (%s:%s/%s)",
            settings.DB_HOST, settings.DB_PORT, settings.DB_NAME,
        )

    async def disconnect(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            logger.info("PostgreSQL: пул соединений закрыт")

    async def init_models(self) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
            logger.info("PostgreSQL: схема инициализирована")

    async def upsert_client(
        self,
        *,
        name: str,
        ig_business_id: str,
        page_access_token: str,
        manager_chat_id: int
    ) -> int:
        assert self.pool is not None
        query = """
            INSERT INTO clients (name, ig_business_id, page_access_token, manager_chat_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (ig_business_id) DO UPDATE
                SET page_access_token = EXCLUDED.page_access_token,
                    manager_chat_id = EXCLUDED.manager_chat_id
            RETURNING id;
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, name, ig_business_id, page_access_token, manager_chat_id)
            assert row is not None
            return row["id"]

    async def add_lead(
        self,
        *,
        client_id: int,
        ig_comment_id: str,
        ig_media_id: str,
        ig_user_id: str,
        ig_username: str | None,
        comment_text: str,
        post_type: str,  # 'organic' | 'ad'
    ) -> int:
        assert self.pool is not None
        query = """
            INSERT INTO leads (client_id, ig_comment_id, ig_media_id,
                               ig_user_id, ig_username, comment_text, post_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7::post_type_enum)
            ON CONFLICT (ig_comment_id) DO UPDATE
                SET comment_text = EXCLUDED.comment_text
            RETURNING id;
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                client_id,
                ig_comment_id,
                ig_media_id,
                ig_user_id,
                ig_username,
                comment_text,
                post_type  # Добавлен недостающий аргумент
            )
            assert row is not None
            return row["id"]

    async def get_client_by_id(self, ig_business_id: str) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT * FROM clients
                WHERE ig_business_id = $1
                  AND is_active = TRUE
                  AND (
                    (subscription_expires_at IS NOT NULL AND subscription_expires_at > now())
                    OR (trial_starts_at + $2::interval > now())
                  )
                """,
                ig_business_id,
                timedelta(days=settings.TRIAL_DAYS),
            )

    async def get_client_by_manager_chat_id(self, manager_chat_id: int) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT * FROM clients WHERE manager_chat_id = $1
                """,
                manager_chat_id,
            )

    async def get_client_billing(self, ig_business_id: str) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT name, ig_business_id, is_active, trial_starts_at, subscription_expires_at
                FROM clients WHERE ig_business_id = $1
                """,
                ig_business_id,
            )

    async def list_clients(self) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT name, ig_business_id, is_active, trial_starts_at,
                       subscription_expires_at, google_sheet_id
                FROM clients
                ORDER BY created_at DESC
                """
            )

    async def set_client_sheet_id(self, ig_business_id: str, google_sheet_id: str) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE clients SET google_sheet_id = $2 WHERE ig_business_id = $1",
                ig_business_id,
                google_sheet_id,
            )

    async def extend_subscription(self, ig_business_id: str, days: int) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                UPDATE clients
                SET subscription_expires_at = GREATEST(COALESCE(subscription_expires_at, now()), now()) + $2::interval
                WHERE ig_business_id = $1
                RETURNING name, ig_business_id, subscription_expires_at
                """,
                ig_business_id,
                timedelta(days=days),
            )

    async def mark_comment_removed(self, lead_id) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.execute(
                """
                UPDATE leads SET is_comment_removed = TRUE WHERE id = $1
                """,
                lead_id,
            )

    async def get_lead_by_id(self, lead_id: int) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT * FROM leads WHERE id = $1
                """,
                lead_id,
            )

    async def update_lead_status(self, lead_id: int, status: str) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE leads SET status = $2::lead_status_enum, updated_at = now() WHERE id = $1
                """,
                lead_id,
                status,
            )

    async def set_lead_sheet_row(self, lead_id: int, sheet_row: int) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE leads SET sheet_row = $2 WHERE id = $1",
                lead_id,
                sheet_row,
            )

    async def get_leads_stats(self) -> dict[str, int]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT post_type, COUNT(*) AS cnt FROM leads GROUP BY post_type"
            )
        stats = {"organic": 0, "ad": 0}
        for row in rows:
            stats[row["post_type"]] = row["cnt"]
        stats["total"] = stats["organic"] + stats["ad"]
        return stats

    async def get_leads_stats_for_manager(self, manager_chat_id: int) -> dict[str, int]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT leads.post_type, COUNT(*) AS cnt
                FROM leads
                JOIN clients ON clients.id = leads.client_id
                WHERE clients.manager_chat_id = $1
                GROUP BY leads.post_type
                """,
                manager_chat_id,
            )
        stats = {"organic": 0, "ad": 0}
        for row in rows:
            stats[row["post_type"]] = row["cnt"]
        stats["total"] = stats["organic"] + stats["ad"]
        return stats

    async def get_recent_leads_for_manager(self, manager_chat_id: int, limit: int = 5) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT leads.id, leads.ig_username, leads.post_type, leads.phone_number,
                       leads.status, leads.created_at
                FROM leads
                JOIN clients ON clients.id = leads.client_id
                WHERE clients.manager_chat_id = $1
                ORDER BY leads.created_at DESC
                LIMIT $2
                """,
                manager_chat_id,
                limit,
            )

    async def close_lead(self, lead_id: int, manager_chat_id: int) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                UPDATE leads
                SET status = 'closed'::lead_status_enum, updated_at = now()
                FROM clients
                WHERE leads.client_id = clients.id
                  AND leads.id = $1
                  AND clients.manager_chat_id = $2
                RETURNING leads.id, leads.ig_username
                """,
                lead_id,
                manager_chat_id,
            )

    async def save_lead_phone(
        self,
        *,
        ig_business_id: str,
        ig_user_id: str,
        phone_number: str,
    ) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                UPDATE leads
                SET phone_number = $3,
                    status = 'phone_received'::lead_status_enum,
                    updated_at = now()
                FROM clients
                WHERE leads.client_id = clients.id
                  AND clients.ig_business_id = $1
                  AND leads.ig_user_id = $2
                  AND leads.status IN ('new', 'notified', 'phone_requested')
                RETURNING leads.id, leads.client_id, leads.ig_username, leads.post_type,
                          leads.is_comment_removed, leads.status, leads.sheet_row,
                          clients.manager_chat_id, clients.name AS client_name,
                          clients.google_sheet_id;
                """,
                ig_business_id,
                ig_user_id,
                phone_number,
            )

    async def toggle_client_active(self, ig_business_id: str) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                UPDATE clients SET is_active = NOT is_active
                WHERE ig_business_id = $1
                RETURNING name, ig_business_id, is_active
                """,
                ig_business_id,
            )

    async def get_recent_leads(self, limit: int = 10) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT leads.ig_username, leads.post_type, leads.phone_number,
                       leads.status, leads.created_at, clients.name AS client_name
                FROM leads
                JOIN clients ON clients.id = leads.client_id
                ORDER BY leads.created_at DESC
                LIMIT $1
                """,
                limit,
            )
