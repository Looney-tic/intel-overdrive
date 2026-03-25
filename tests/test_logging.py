"""OPS-04: Structured logging — JSON renderer active in production mode."""
import pytest
import structlog


def test_json_renderer_active_in_production():
    """OPS-04: configure_logging with ENVIRONMENT=production uses JSONRenderer."""
    import src.core.logger as logger_module

    logger_module._configured = False  # reset idempotency flag

    from src.core.logger import configure_logging
    from src.core.config import Settings

    prod_settings = Settings(
        ENVIRONMENT="production",
        ANTHROPIC_API_KEY="sk-ant-fake",
        VOYAGE_API_KEY="pa-fake",
        MAILGUN_WEBHOOK_SIGNING_KEY="mg-fake-signing-key",
    )

    with pytest.MonkeyPatch().context() as m:
        m.setattr("src.core.logger.get_settings", lambda: prod_settings)
        logger_module._configured = False
        configure_logging()

    # Inspect structlog configuration to confirm JSONRenderer is the last processor
    config = structlog.get_config()
    last_processor = config["processors"][-1]
    assert isinstance(
        last_processor, structlog.processors.JSONRenderer
    ), f"Expected JSONRenderer in production, got {type(last_processor)}"
    logger_module._configured = False  # cleanup for subsequent tests


def test_console_renderer_in_development():
    """OPS-04: configure_logging with ENVIRONMENT=development uses ConsoleRenderer."""
    import src.core.logger as logger_module

    logger_module._configured = False

    from src.core.logger import configure_logging
    from src.core.config import Settings

    dev_settings = Settings(ENVIRONMENT="development")

    with pytest.MonkeyPatch().context() as m:
        m.setattr("src.core.logger.get_settings", lambda: dev_settings)
        logger_module._configured = False
        configure_logging()

    config = structlog.get_config()
    last_processor = config["processors"][-1]
    assert isinstance(
        last_processor, structlog.dev.ConsoleRenderer
    ), f"Expected ConsoleRenderer in development, got {type(last_processor)}"
    logger_module._configured = False  # cleanup
