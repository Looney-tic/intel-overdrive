"""CLI configuration: paths, constants, and API URL resolution."""

import os
from pathlib import Path

KEYRING_SERVICE = "overdrive-intel"
KEYRING_USERNAME = "api-key"

CONFIG_DIR = Path.home() / ".config" / "overdrive-intel"
CONFIG_KEY_PATH = CONFIG_DIR / "key"

DEFAULT_API_URL = "https://inteloverdrive.com"


def get_api_url() -> str:
    """Resolve API base URL: env var → config file → default."""
    env = os.environ.get("OVERDRIVE_API_URL")
    if env:
        return env
    url_path = CONFIG_DIR / "api_url"
    if url_path.exists():
        return url_path.read_text().strip()
    return DEFAULT_API_URL
