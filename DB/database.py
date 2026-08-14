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
ALTER TABLE clients ADD COLUMN IF NOT EXISTS custom_direct_text TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS custom_thank_you_text TEXT;

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
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE leads DROP COLUMN IF EXISTS sheet_row;

CREATE INDEX IF NOT EXISTS idx_leads_ig_user_id ON leads(ig_user_id);

-- Granular Media Targeting: бот обрабатывает комментарии СТРОГО под теми
-- публикациями, что явно добавлены сюда через Telegram-конструктор. Пустая
-- таблица = бот молчит на всём аккаунте (осознанное поведение, не баг).
CREATE TABLE IF NOT EXISTS monitored_media (
    id                SERIAL PRIMARY KEY,
    ig_business_id    VARCHAR(64) NOT NULL REFERENCES clients(ig_business_id) ON DELETE CASCADE,
    media_id          VARCHAR(64) NOT NULL UNIQUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_monitored_media_ig_business_id ON monitored_media(ig_business_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'trigger_type_enum') THEN
        CREATE TYPE trigger_type_enum AS ENUM ('all_comments', 'keywords');
    END IF;
END$$;

-- custom_text переименован в reply_text (ChatPlace-style flow: пост ->
-- условия -> текст ответа -> запуск). Переименование идемпотентно —
-- на повторных стартах колонки custom_text уже не будет, блок не сработает.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'monitored_media' AND column_name = 'custom_text'
    ) THEN
        ALTER TABLE monitored_media RENAME COLUMN custom_text TO reply_text;
    END IF;
END$$;

ALTER TABLE monitored_media ADD COLUMN IF NOT EXISTS reply_text TEXT;
ALTER TABLE monitored_media ADD COLUMN IF NOT EXISTS trigger_type trigger_type_enum NOT NULL DEFAULT 'keywords';
ALTER TABLE monitored_media ADD COLUMN IF NOT EXISTS keywords_list TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE monitored_media ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;
-- NULL = доверяем автоопределению Meta (media_product_type) как и раньше.
-- Заполнено — жёсткий ручной override на случай, если Meta ошибается на конкретном посте.
ALTER TABLE monitored_media ADD COLUMN IF NOT EXISTS post_type_override post_type_enum;
ALTER TABLE monitored_media ADD COLUMN IF NOT EXISTS thank_you_text TEXT;
ALTER TABLE monitored_media ADD COLUMN IF NOT EXISTS title TEXT;
-- Раньше скрытие коммента было жёстко вшито в post_type == 'ad' — теперь
-- отдельный тумблер на карточке. DEFAULT TRUE сохраняет прежнее поведение
-- для всех уже существующих постов, никто ничего не теряет молча.
ALTER TABLE monitored_media ADD COLUMN IF NOT EXISTS hide_comments BOOLEAN NOT NULL DEFAULT TRUE;

-- Удалено по прямому решению владельца: никаких "ИИ-агентов"/абстрактных
-- диалогов в проекте, только жёсткий алгоритмический щит. Идемпотентный
-- DROP снимает и колонку, и таблицу с любой БД, где успела прожить прошлая
-- версия схемы — тот же паттерн, что и DROP COLUMN sheet_row выше.
ALTER TABLE monitored_media DROP COLUMN IF EXISTS ai_prompt;
DROP TABLE IF EXISTS ai_conversation_messages;
DROP TYPE IF EXISTS ai_role_enum;
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

    async def count_active_media(self, ig_business_id: str) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM monitored_media WHERE ig_business_id = $1 AND is_active = TRUE",
                ig_business_id,
            )

    async def get_monitored_media(self, media_id: str) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT * FROM monitored_media WHERE media_id = $1",
                media_id,
            )

    async def add_media_to_monitor(self, ig_business_id: str, media_id: str, title: str | None = None) -> str:
        """Возвращает 'created' (новая запись), 'exists' (уже добавлен этим же
        аккаунтом ранее — настройки трогать нельзя) или 'conflict' (занят другим
        аккаунтом). Раньше вызывающий код не мог отличить 'created' от 'exists'
        и всегда гнал по мастеру заново, затирая уже сохранённые условия/текст."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO monitored_media (ig_business_id, media_id, trigger_type, keywords_list, is_active, title)
                VALUES ($1, $2, 'keywords', $3, FALSE, $4)
                ON CONFLICT (media_id) DO NOTHING
                RETURNING id;
                """,
                ig_business_id,
                media_id,
                settings.LEAD_KEYWORDS,  # стартовые ключевые слова — можно перенастроить на Шаге 2
                title,
            )
            if row is not None:
                return "created"

            owner = await conn.fetchval(
                "SELECT ig_business_id FROM monitored_media WHERE media_id = $1", media_id
            )
            return "exists" if owner == ig_business_id else "conflict"

    async def set_media_trigger(
        self, manager_chat_id: int, media_id: str, trigger_type: str, keywords_list: list[str]
    ) -> bool:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE monitored_media
                SET trigger_type = $3::trigger_type_enum, keywords_list = $4
                FROM clients
                WHERE monitored_media.media_id = $1
                  AND monitored_media.ig_business_id = clients.ig_business_id
                  AND clients.manager_chat_id = $2
                """,
                media_id,
                manager_chat_id,
                trigger_type,
                keywords_list,
            )
        return result == "UPDATE 1"

    async def set_media_reply_text(self, manager_chat_id: int, media_id: str, reply_text: str | None) -> bool:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE monitored_media
                SET reply_text = $3
                FROM clients
                WHERE monitored_media.media_id = $1
                  AND monitored_media.ig_business_id = clients.ig_business_id
                  AND clients.manager_chat_id = $2
                """,
                media_id,
                manager_chat_id,
                reply_text,
            )
        return result == "UPDATE 1"

    async def set_media_thank_you_text(self, manager_chat_id: int, media_id: str, thank_you_text: str | None) -> bool:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE monitored_media
                SET thank_you_text = $3
                FROM clients
                WHERE monitored_media.media_id = $1
                  AND monitored_media.ig_business_id = clients.ig_business_id
                  AND clients.manager_chat_id = $2
                """,
                media_id,
                manager_chat_id,
                thank_you_text,
            )
        return result == "UPDATE 1"

    async def set_media_post_type_override(
        self, manager_chat_id: int, media_id: str, post_type_override: str | None
    ) -> bool:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE monitored_media
                SET post_type_override = $3::post_type_enum
                FROM clients
                WHERE monitored_media.media_id = $1
                  AND monitored_media.ig_business_id = clients.ig_business_id
                  AND clients.manager_chat_id = $2
                """,
                media_id,
                manager_chat_id,
                post_type_override,
            )
        return result == "UPDATE 1"

    async def set_media_active(self, manager_chat_id: int, media_id: str, is_active: bool) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                UPDATE monitored_media
                SET is_active = $3
                FROM clients
                WHERE monitored_media.media_id = $1
                  AND monitored_media.ig_business_id = clients.ig_business_id
                  AND clients.manager_chat_id = $2
                RETURNING monitored_media.*;
                """,
                media_id,
                manager_chat_id,
                is_active,
            )

    async def set_media_hide_comments(
        self, manager_chat_id: int, media_id: str, hide_comments: bool
    ) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                UPDATE monitored_media
                SET hide_comments = $3
                FROM clients
                WHERE monitored_media.media_id = $1
                  AND monitored_media.ig_business_id = clients.ig_business_id
                  AND clients.manager_chat_id = $2
                RETURNING monitored_media.*;
                """,
                media_id,
                manager_chat_id,
                hide_comments,
            )

    async def delete_monitored_media(self, manager_chat_id: int, media_id: str) -> bool:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM monitored_media
                USING clients
                WHERE monitored_media.media_id = $1
                  AND monitored_media.ig_business_id = clients.ig_business_id
                  AND clients.manager_chat_id = $2
                """,
                media_id,
                manager_chat_id,
            )
        return result == "DELETE 1"

    async def list_monitored_media(self, ig_business_id: str) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM monitored_media WHERE ig_business_id = $1 ORDER BY created_at DESC",
                ig_business_id,
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
                          leads.is_comment_removed, leads.status,
                          clients.manager_chat_id, clients.name AS client_name,
                          clients.google_sheet_id, clients.page_access_token,
                          (SELECT thank_you_text FROM monitored_media
                           WHERE monitored_media.media_id = leads.ig_media_id) AS thank_you_text;
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
