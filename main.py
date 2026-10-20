"""Compatibility entry point for running the ERP API with uvicorn."""

from backend.app.main import app

__all__ = ["app"]
