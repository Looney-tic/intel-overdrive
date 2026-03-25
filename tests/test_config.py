"""
FOUND-07: Verify Settings loads with defaults, validates production secrets,
and that configure_logging() is idempotent.
"""
import os
import pytest
from pydantic import ValidationError


def test_settings_loads_defaults_in_development():
    """FOUND-07: Settings loads with defaults when ENVIRONMENT=development."""
    # conftest already sets ENVIRONMENT=development
    from src.core.config import Settings

    s = Settings()
    assert s.ENVIRONMENT == "development"
    assert isinstance(s.DAILY_SPEND_LIMIT, float)
    assert s.DAILY_SPEND_LIMIT > 0
    assert isinstance(s.LLM_MODEL, str)
    assert len(s.LLM_MODEL) > 0
    assert s.EMBEDDING_MODEL == "voyage-3.5-lite"
    assert s.EMBEDDING_DIM == 1024


def test_settings_production_rejects_missing_anthropic_key():
    """FOUND-07: Production mode raises ValueError when ANTHROPIC_API_KEY is empty."""
    from src.core.config import Settings

    with pytest.raises((ValueError, ValidationError)):
        Settings(
            ENVIRONMENT="production",
            ANTHROPIC_API_KEY=None,
            VOYAGE_API_KEY="some-voyage-key",
        )


def test_settings_production_rejects_missing_voyage_key():
    """FOUND-07: Production mode raises ValueError when VOYAGE_API_KEY is empty."""
    from src.core.config import Settings

    with pytest.raises((ValueError, ValidationError)):
        Settings(
            ENVIRONMENT="production",
            ANTHROPIC_API_KEY="some-anthropic-key",
            VOYAGE_API_KEY=None,
        )


def test_settings_production_accepts_all_keys():
    """FOUND-07 + C-4: Production mode succeeds when all required keys are set."""
    from src.core.config import Settings

    s = Settings(
        ENVIRONMENT="production",
        ANTHROPIC_API_KEY="sk-ant-real-key",
        VOYAGE_API_KEY="pa-real-voyage-key",
        MAILGUN_WEBHOOK_SIGNING_KEY="mg-signing-key",
    )
    assert s.ENVIRONMENT == "production"
    assert s.ANTHROPIC_API_KEY == "sk-ant-real-key"
    assert s.VOYAGE_API_KEY == "pa-real-voyage-key"
    assert s.MAILGUN_WEBHOOK_SIGNING_KEY == "mg-signing-key"


def test_settings_production_rejects_missing_mailgun_key():
    """C-4: Production mode raises ValueError when MAILGUN_WEBHOOK_SIGNING_KEY is missing."""
    from src.core.config import Settings

    with pytest.raises((ValueError, ValidationError)):
        Settings(
            ENVIRONMENT="production",
            ANTHROPIC_API_KEY="sk-ant-real-key",
            VOYAGE_API_KEY="pa-real-voyage-key",
            MAILGUN_WEBHOOK_SIGNING_KEY=None,
        )


def test_configure_logging_is_idempotent():
    """FOUND-07: configure_logging() can be called twice without error."""
    # Reset the internal flag so we can test double-init
    import src.core.logger as logger_module

    logger_module._configured = False

    from src.core.logger import configure_logging

    configure_logging()  # First call — should set up structlog
    configure_logging()  # Second call — should be a no-op

    # If we got here without an exception, the function is idempotent
    assert logger_module._configured is True
