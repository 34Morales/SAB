"""Environment based application settings."""

import json
import os
from functools import lru_cache
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "ERP Empresarial")
        self.app_version = os.getenv("APP_VERSION", "0.1.0")
        self.environment = os.getenv("ENVIRONMENT", "development")
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./erp.db")
        self.auth_throttle_pepper = os.getenv("AUTH_THROTTLE_PEPPER", "development-only-pepper-change-before-deploy")
        self.mfa_encryption_key = os.getenv("MFA_ENCRYPTION_KEY", "")
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
        self.smtp_username = os.getenv("SMTP_USERNAME", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", "")
        try:
            self.cors_origins = json.loads(os.getenv("CORS_ORIGINS", "[]"))
        except json.JSONDecodeError as exc:
            raise ValueError("CORS_ORIGINS must be a JSON array") from exc
        if not isinstance(self.cors_origins, list) or not all(isinstance(origin, str) for origin in self.cors_origins):
            raise ValueError("CORS_ORIGINS must be a JSON array of origin strings")
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535")
        if self.environment.lower() == "production":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("DATABASE_URL must use PostgreSQL with psycopg in production")
            if len(self.auth_throttle_pepper) < 32 or self.auth_throttle_pepper in {
                "development-only-pepper-change-before-deploy", "replace-with-a-long-random-secret"
            }:
                raise ValueError("AUTH_THROTTLE_PEPPER must be a unique random secret of at least 32 characters")
            if not self.mfa_encryption_key:
                raise ValueError("MFA_ENCRYPTION_KEY must be set in production")
            from cryptography.fernet import Fernet

            try:
                Fernet(self.mfa_encryption_key.encode())
            except (ValueError, TypeError) as exc:
                raise ValueError("MFA_ENCRYPTION_KEY must be a valid Fernet key") from exc
            if not self.smtp_host or not self.smtp_from:
                raise ValueError("SMTP_HOST and SMTP_FROM are required in production for password recovery")
            if bool(self.smtp_username) != bool(self.smtp_password):
                raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be set together")
            if not self.public_base_url.startswith("https://"):
                raise ValueError("PUBLIC_BASE_URL must use HTTPS in production")
            for origin in self.cors_origins:
                parsed = urlsplit(origin)
                if origin == "*" or parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
                    raise ValueError("Production CORS_ORIGINS must contain exact HTTPS origins without paths")


@lru_cache
def get_settings() -> Settings:
    return Settings()
