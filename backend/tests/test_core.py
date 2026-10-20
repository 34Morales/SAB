import pytest
from cryptography.fernet import Fernet

from backend.app.core.config import Settings
from backend.app.main import app
from backend.app.api.routes import password_reset_page


def test_openapi_contains_identity_endpoints():
    paths = app.openapi()["paths"]
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/identity/users" in paths
    assert "/api/v1/security/events" in paths
    assert "/api/v1/branches" in paths
    assert "/api/v1/branches/{branch_id}" in paths
    assert "/api/v1/companies/current" in paths
    assert "/api/v1/catalog/categories" in paths
    assert "/api/v1/catalog/products" in paths


def test_password_reset_page_uses_no_store_and_nonce_csp():
    response = password_reset_page()
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'nonce-" in response.headers["content-security-policy"]
    assert "Referrer-Policy" in response.headers


def test_production_configuration_rejects_sqlite(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./erp.db")
    monkeypatch.setenv("AUTH_THROTTLE_PEPPER", "random-production-secret-that-is-long-enough")
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "ERP <no-reply@example.test>")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://erp.example.test")
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings()


def test_production_configuration_rejects_wildcard_cors(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://erp:secret@db.example.test/erp")
    monkeypatch.setenv("AUTH_THROTTLE_PEPPER", "random-production-secret-that-is-long-enough")
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "ERP <no-reply@example.test>")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://erp.example.test")
    monkeypatch.setenv("CORS_ORIGINS", '["*"]')
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        Settings()
