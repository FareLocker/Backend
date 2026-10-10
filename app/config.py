from pydantic import computed_field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_user: str = "farelocker"
    postgres_password: str = "farelocker"
    postgres_db: str = "farelocker"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    cors_origins: str = "http://localhost:3000"

    # auth
    secret_key: str = "change-me-in-prod"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days

    # pricing economics
    base_lambda: float = 0.30  # inventory loading
    vol_prior: float = 0.35  # vol shrinkage prior

    # scheduler cadence
    refit_interval_minutes: int = 60
    settlement_interval_minutes: int = 60

    scraper_enabled: bool = False
    fares_csv: str = "data/fares.csv"
    scraper_sleep_seconds: float = 2.0

    model_config = {"env_file": ".env", "extra": "ignore"}

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def allowed_origins(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]


settings = Settings()
