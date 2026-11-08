import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.control_db_url = os.environ.get("CONTROL_DB_URL", "")
        self.secret_key = os.environ.get("SECRET_KEY", "")
        self.admin_password = os.environ.get("ADMIN_PASSWORD", "")
        self.run_token = os.environ.get("RUN_TOKEN", "")
        self.run_interval_minutes = _int("RUN_INTERVAL_MINUTES", 60)
        self.min_run_gap_minutes = _int("MIN_RUN_GAP_MINUTES", 30)
        self.check_timeout = _int("CHECK_TIMEOUT_SECONDS", 15)
        self.check_retries = _int("CHECK_RETRIES", 2)
        self.run_budget = _int("RUN_BUDGET_SECONDS", 90)
        self.history = _int("HISTORY_PER_SERVICE", 5)
        self.secure_cookies = os.environ.get("COOKIE_SECURE", "true").lower() != "false"

    def validate(self) -> None:
        missing = [
            n
            for n, v in (
                ("CONTROL_DB_URL", self.control_db_url),
                ("SECRET_KEY", self.secret_key),
                ("ADMIN_PASSWORD", self.admin_password),
                ("RUN_TOKEN", self.run_token),
            )
            if not v
        ]
        if missing:
            raise RuntimeError("Missing required environment variables: " + ", ".join(missing))


settings = Settings()
