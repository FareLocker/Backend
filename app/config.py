from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./farelocker.db"
    supabase_url: str | None = None
    supabase_jwt_audience: str = "authenticated"
    cors_origins: str = "http://localhost:3000"

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

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def supabase_issuer(self) -> str | None:
        if self.supabase_url is None:
            return None
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str | None:
        issuer = self.supabase_issuer
        return f"{issuer}/.well-known/jwks.json" if issuer else None


settings = Settings()
