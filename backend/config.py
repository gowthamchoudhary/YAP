"""Central, unforgiving env-var access.

Every module that talks to the outside world reads its configuration through
this file so a missing key is ALWAYS reported as:

    Missing required environment variable: <NAME. ...>

and never as a mystery 401 from a provider.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# backend/.env first (canonical location), then a repo-root .env as a fallback.
_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_BACKEND_DIR / ".env")
load_dotenv(_BACKEND_DIR.parent / ".env")


class MissingEnvError(Exception):
    """Raised when a required environment variable is not set."""

    def __init__(self, name: str, hint: str = "") -> None:
        msg = (
            f"Missing required environment variable: {name}. "
            f"Set it in backend/.env (copy .env.example).{(' ' + hint) if hint else ''}"
        )
        super().__init__(msg)
        self.name = name


def backend_dir() -> Path:
    return _BACKEND_DIR


def get(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def require(name: str, hint: str = "") -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        raise MissingEnvError(name, hint)
    return value.strip()


def get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default
