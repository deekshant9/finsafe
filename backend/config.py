"""Central env config. Loaded once at import time."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


FRONTEND_URL = _get("FRONTEND_URL", "http://localhost:3000").rstrip("/")
CORS_ORIGINS = [o.strip() for o in _get("CORS_ORIGINS", FRONTEND_URL).split(",") if o.strip()]

GEMINI_API_KEY = _get("GEMINI_API_KEY")
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-flash-latest")

ZOHO_ACCOUNTS_URL = _get("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.in").rstrip("/")
ZOHO_CLIENT_ID = _get("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = _get("ZOHO_CLIENT_SECRET")
ZOHO_REDIRECT_URI = _get("ZOHO_REDIRECT_URI", "http://localhost:8000/auth/zoho/callback")

NETSUITE_CLIENT_ID = _get("NETSUITE_CLIENT_ID")
NETSUITE_CERTIFICATE_ID = _get("NETSUITE_CERTIFICATE_ID")
NETSUITE_PRIVATE_KEY_PATH = _get("NETSUITE_PRIVATE_KEY_PATH", str(BASE_DIR / "netsuite_private_key.pem"))
NETSUITE_JWT_ALG = _get("NETSUITE_JWT_ALG", "PS256")
