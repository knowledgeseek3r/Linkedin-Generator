import os
import re
import yaml
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

UNIT_TO_DAYS = {
    "days": 1,
    "weeks": 7,
    "months": 30,
}

VALID_RESEARCH_DEPTHS = {"shallow", "deep"}
VALID_CTA_TYPES = {"frage_an_community", "ressource_teilen", "meinung_einfordern", "newsletter_link"}


def _resolve_env_vars(value: str) -> str:
    """Replace ${VAR} references with environment variable values."""
    def replacer(match):
        var_name = match.group(1)
        val = os.getenv(var_name)
        if val is None:
            raise ValueError(f"Environment variable '{var_name}' is not set")
        return val
    return re.sub(r"\$\{(\w+)\}", replacer, value)


def _resolve_strings(obj):
    """Recursively resolve env var references in all string values."""
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _resolve_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_strings(i) for i in obj]
    return obj


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    config = _resolve_strings(raw)

    # Validate required fields
    if not config.get("keywords"):
        raise ValueError("config.yaml: 'keywords' must be a non-empty list")

    depth = config.get("research_depth", "shallow")
    if depth not in VALID_RESEARCH_DEPTHS:
        raise ValueError(f"config.yaml: 'research_depth' must be one of {VALID_RESEARCH_DEPTHS}")

    cta = config.get("cta", {})
    if cta.get("enabled") and cta.get("type") not in VALID_CTA_TYPES:
        raise ValueError(f"config.yaml: 'cta.type' must be one of {VALID_CTA_TYPES}")

    # Resolve date_from once at startup
    time_range = config.get("scrape_time_range", {"unit": "weeks", "value": 2})
    unit = time_range.get("unit", "weeks")
    value = int(time_range.get("value", 2))
    if unit not in UNIT_TO_DAYS:
        raise ValueError(f"config.yaml: 'scrape_time_range.unit' must be one of {list(UNIT_TO_DAYS.keys())}")

    days = value * UNIT_TO_DAYS[unit]
    config["date_from"] = datetime.now(timezone.utc) - timedelta(days=days)

    # Validate email_notification section
    email_cfg = config.get("email_notification", {})
    if email_cfg.get("enabled"):
        for key in ["smtp_host", "smtp_port", "sender_email", "sender_password",
                    "recipient_email", "reply_trigger"]:
            if not email_cfg.get(key):
                raise ValueError(
                    f"config.yaml: 'email_notification.{key}' is required "
                    "when email_notification.enabled is true"
                )

    # Validate linkedin_posting section
    li_cfg = config.get("linkedin_posting", {})
    if li_cfg.get("enabled"):
        for key in ["access_token", "person_urn"]:
            if not li_cfg.get(key):
                raise ValueError(
                    f"config.yaml: 'linkedin_posting.{key}' is required "
                    "when linkedin_posting.enabled is true"
                )
        if not li_cfg["person_urn"].startswith("urn:li:person:"):
            raise ValueError(
                "config.yaml: 'linkedin_posting.person_urn' must start with 'urn:li:person:'"
            )

    return config
