from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # === Telegram ===
    BOT_TOKEN: str
    MANAGER_CHAT_ID: int  # Запасной ID менеджера

    # === PostgreSQL ===
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "leadarmor"
    DB_USER: str = "a1111"
    DB_PASSWORD: str

    # === Meta / Instagram Graph API ===
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_VERIFY_TOKEN: str = ""
    META_PAGE_ACCESS_TOKEN: str = ""
    META_OAUTH_REDIRECT_URI: str = ""

    # Отдельные App ID/Secret для Instagram Business Login (instagram.com/oauth/authorize) —
    # НЕ совпадают с основным META_APP_ID/META_APP_SECRET, которые используются для
    # graph.facebook.com и подписи вебхуков (X-Hub-Signature).
    META_INSTAGRAM_APP_ID: str = ""
    META_INSTAGRAM_APP_SECRET: str = ""

    # === Google Sheets ===
    GOOGLE_SHEETS_CREDENTIALS_FILE: str = "credentials.json"
    GOOGLE_SHEETS_SPREADSHEET_ID: str = ""

    # OAuth от личного Google-аккаунта владельца — нужен, чтобы создавать
    # отдельный файл на каждого клиента (у service account своей квоты Drive нет).
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_OAUTH_REDIRECT_URI: str = ""
    GOOGLE_OAUTH_REFRESH_TOKEN: str = ""

    LEAD_KEYWORDS: list[str] = [
        "купить", "цена", "сколько стоит", "+", "малумот",
        "narxi", "нархи", "qancha", "қанча",
        "sotib olaman", "сотиб оламан", "buyurtma",
    ]

    TRIAL_DAYS: int = 3
    SUBSCRIPTION_PRICE_USD: int = 50

    # Обязательные права и подписки вебхука в Meta App Dashboard —
    # без них hide_comment/send_private_reply/process_direct_message не заработают.
    REQUIRED_META_PERMISSIONS: list[str] = [
        "instagram_business_basic",
        "instagram_business_manage_comments",
        "instagram_business_manage_messages",
    ]
    REQUIRED_META_WEBHOOK_FIELDS: list[str] = [
        "comments",
        "messages",
        "messaging_postbacks",
    ]

    # Важно: dsn с отступом в 4 пробела внутри класса Settings!
    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# Создаём объект настроек в самом конце, когда класс полностью описан
settings = Settings()