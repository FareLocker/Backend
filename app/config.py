from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg2://localhost/farelocker"

    # pricing economics
    base_lambda: float = 0.30  # inventory loading
    vol_prior: float = 0.35  # vol shrinkage prior

    # scheduler cadence
    refit_interval_minutes: int = 60

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
