"""
Central place for environment-driven configuration.

Nothing in this file is a secret — actual values come from the environment
(see .env.example). Import `config` and read attributes off it; don't reach
into os.environ elsewhere in the app.
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Config:
    # --- Azure OpenAI ---
    AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
    AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")

    # --- Twilio ---
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_API_KEY_SID = os.environ.get("TWILIO_API_KEY_SID", "")
    TWILIO_API_KEY_SECRET = os.environ.get("TWILIO_API_KEY_SECRET", "")
    TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")

    # --- Staff notification email ---
    STAFF_EMAIL = os.environ.get("STAFF_EMAIL", "")

    # SMTP (used if SENDGRID_API_KEY is not set)
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")

    # SendGrid (used instead of SMTP if set)
    SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")

    # --- App ---
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "conversations.db")
    RESTAURANT_NAME = os.environ.get("RESTAURANT_NAME", "The Sample Kitchen")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # Set to "true" to skip Twilio signature validation (local testing only).
    SKIP_TWILIO_SIGNATURE_VALIDATION = (
        os.environ.get("SKIP_TWILIO_SIGNATURE_VALIDATION", "false").lower() == "true"
    )

    def validate_for_startup(self) -> None:
        """Fail fast and loud if required secrets are missing."""
        _require("AZURE_OPENAI_API_KEY")
        _require("AZURE_OPENAI_ENDPOINT")
        _require("AZURE_OPENAI_DEPLOYMENT")
        _require("TWILIO_ACCOUNT_SID")
        _require("TWILIO_AUTH_TOKEN")
        _require("TWILIO_PHONE_NUMBER")
        _require("STAFF_EMAIL")
        if not self.SENDGRID_API_KEY and not self.SMTP_HOST:
            raise RuntimeError(
                "Configure either SENDGRID_API_KEY or SMTP_HOST/SMTP_PORT/SMTP_USER/"
                "SMTP_PASSWORD/SMTP_FROM for staff email delivery."
            )

        # Soft check: warn (never fail) if the typed contact mirror in
        # business_info.py has drifted from kb_data/business.md. Imported here
        # rather than at module top to keep config free of app-module imports.
        from business_info import verify_against_business_md

        for warning in verify_against_business_md():
            logger.warning("Business info drift: %s", warning)


config = Config()
