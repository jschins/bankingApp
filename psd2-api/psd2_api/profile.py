"""Per-person runtime configuration ("profile") for the family executable.

A *profile* bundles everything one family member's build needs:

  - their Enable Banking application id + private key (the PSD2 credentials),
  - which bank to authorize (country + ASPSP) and the redirect URL,
  - which person folder to write to on bankingApp-server, and the server URL + key.

When packaged with PyInstaller, ``profile.json`` and the private key are bundled
into the executable and read from the unpacked bundle directory at runtime, so
each family member gets a self-contained ``.exe`` with their own credentials
baked in. During development (not frozen) every value falls back to environment
variables / ``.env`` so the existing CLI keeps working unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

PROFILE_NAME = "profile.json"
# Admin (non-frozen) layout: one folder per person plus a shared server file.
PROFILES_DIR = Path("packaging") / "profiles"
SERVER_FILE = Path("packaging") / "server.json"


@dataclass
class Profile:
    person: str
    app_id: str
    key_path: Path
    country: str
    aspsp: str
    redirect_url: str
    server_url: str
    server_api_key: str

    def missing(self) -> list[str]:
        """Names of required fields that are empty (for a friendly error)."""
        required = {
            "person": self.person,
            "app_id (ENABLEBANKING_APP_ID)": self.app_id,
            "server_url (bankingApp_SERVER_URL)": self.server_url,
        }
        return [name for name, value in required.items() if not value]


def _bundle_dir() -> Path | None:
    """Directory PyInstaller unpacks bundled data into, or ``None`` in dev."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def _find_profile_file(person: str | None = None) -> Path | None:
    """Locate ``profile.json``.

    When ``person`` is given (admin use), the matching
    ``packaging/profiles/<person>/profile.json`` is preferred so one checkout can
    fetch for several people. Otherwise: explicit override, bundled, beside exe,
    then cwd (the family-member / frozen path).
    """
    candidates: list[Path] = []
    override = os.environ.get("PSD2_PROFILE")
    if override:
        candidates.append(Path(override))
    if person:
        candidates.append(PROFILES_DIR / person / PROFILE_NAME)
    bundle = _bundle_dir()
    if bundle is not None:
        candidates.append(bundle / PROFILE_NAME)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / PROFILE_NAME)
    candidates.append(Path.cwd() / PROFILE_NAME)
    return next((c for c in candidates if c.is_file()), None)


def list_profiles() -> list[str]:
    """People who have a ``packaging/profiles/<person>/profile.json`` (admin)."""
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(
        p.name
        for p in PROFILES_DIR.iterdir()
        if p.is_dir() and (p / PROFILE_NAME).is_file()
    )


def _load_server_settings() -> dict:
    """Read the shared ``packaging/server.json`` (URL + key), or empty if absent."""
    if SERVER_FILE.is_file():
        try:
            return json.loads(SERVER_FILE.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
    return {}


def load_profile(person: str | None = None) -> Profile:
    """Build the active :class:`Profile` from a profile file and/or environment.

    Pass ``person`` (admin) to load ``packaging/profiles/<person>``; the shared
    server settings come from ``packaging/server.json`` (then ``.env``).
    """
    data: dict = {}
    src_dir: Path | None = None
    profile_file = _find_profile_file(person)
    if profile_file is not None:
        data = json.loads(profile_file.read_text(encoding="utf-8-sig"))
        src_dir = profile_file.parent

    # Dev fallback: let .env supply anything the profile omits.
    from dotenv import load_dotenv

    load_dotenv()

    server = _load_server_settings()

    def value(key: str, env: str, default: str = "") -> str:
        return str(data.get(key) or os.environ.get(env, default) or "")

    def server_value(key: str, env: str, default: str = "") -> str:
        # A bundled exe's own profile wins. Then the admin's .env (so admin-side
        # commands reach bankingApp-server via localhost), then the shared
        # server.json (the family-facing address baked into builds), then default.
        return str(
            data.get(key)
            or os.environ.get(env, "")
            or server.get(key, "")
            or default
            or ""
        )

    key_file = data.get("key_file") or os.environ.get("ENABLEBANKING_KEY_PATH", "")
    key_path = Path(key_file)
    # A bundled / profile-relative key file resolves next to the profile.
    if key_file and not key_path.is_absolute() and src_dir is not None:
        key_path = src_dir / key_file

    # Hash the server passphrase to get the Bearer token
    api_passphrase = server_value("server_api_passphrase", "bankingApp_SERVER_API_PASSPHRASE")
    api_key = server_value("server_api_key", "bankingApp_SERVER_API_KEY")
    if api_passphrase:
        api_key = hashlib.sha256(api_passphrase.encode("utf-8")).hexdigest()

    return Profile(
        person=value("person", "PSD2_PERSON") or (person or ""),
        app_id=value("app_id", "ENABLEBANKING_APP_ID"),
        key_path=key_path,
        country=value("country", "PSD2_COUNTRY", "NL"),
        aspsp=value("aspsp", "PSD2_ASPSP", "ING"),
        redirect_url=value("redirect_url", "PSD2_REDIRECT_URL", "https://example.com/"),
        server_url=server_value("server_url", "bankingApp_SERVER_URL", "http://localhost:8000"),
        server_api_key=api_key,
    )
