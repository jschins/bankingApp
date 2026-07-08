from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.runtime import data_dir, project_path, secret_dir

_runtime: "AppSettings | None" = None


@dataclass(frozen=True)
class AppSettings:
    person_short: str
    profile_path: Path
    private_key_path: Path
    data_dir: Path
    server_url: str
    server_api_key: str


def get_app_settings() -> AppSettings:
    if _runtime is None:
        raise RuntimeError("Application settings are not initialised")
    return _runtime


def person_from_consent(data_root: Path) -> str | None:
    """Return person short name from a single ``{short}_consent.json`` in *data_root*."""
    persons: list[str] = []
    for path in sorted(data_root.glob("*_consent.json")):
        short = path.name[: -len("_consent.json")].strip()
        if not short:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read {path.name}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{path.name} must be a JSON object")
        person = str(payload.get("person") or "").strip()
        if person != short:
            raise ValueError(
                f"{path.name}: person field is {person!r}, expected {short!r} from filename"
            )
        persons.append(short)

    if len(persons) == 1:
        return persons[0]
    if len(persons) > 1:
        names = ", ".join(f"{p}_consent.json" for p in persons)
        raise FileNotFoundError(
            f"Multiple consent files in {data_root}: {names}. Set bankingApp_PERSON."
        )
    return None


def person_from_secret_profiles() -> str | None:
    profiles = sorted(secret_dir().glob("*_profile.json"))
    persons = [path.name[: -len("_profile.json")].strip() for path in profiles]
    persons = [person for person in persons if person]
    if len(persons) == 1:
        return persons[0]
    if len(persons) > 1:
        names = ", ".join(path.name for path in profiles)
        raise FileNotFoundError(
            f"Multiple profile files in {secret_dir()}: {names}. Set bankingApp_PERSON."
        )
    return None


def resolve_person_short(explicit: str | None = None) -> str:
    if explicit and str(explicit).strip():
        return str(explicit).strip()

    env_person = os.environ.get("bankingApp_PERSON", "").strip()
    if env_person:
        return env_person

    data_root = data_dir()
    consent_person = person_from_consent(data_root)
    if consent_person:
        return consent_person

    profile_person = person_from_secret_profiles()
    if profile_person:
        return profile_person

    raise FileNotFoundError(
        f"Could not determine person short name. Set bankingApp_PERSON or add exactly one "
        f"{{person}}_consent.json in {data_root} or {{person}}_profile.json in {secret_dir()}."
    )


def resolve_private_key_path() -> Path:
    pem_files = sorted(secret_dir().glob("*.pem"))
    if len(pem_files) == 1:
        return pem_files[0].resolve()
    if not pem_files:
        raise FileNotFoundError(f"No .pem private key file found in {secret_dir()}.")
    names = ", ".join(path.name for path in pem_files)
    raise FileNotFoundError(
        f"Expected exactly one .pem file in {secret_dir()}, found: {names}."
    )


def init_app_settings(person_short: str | None = None) -> AppSettings:
    """Resolve runtime paths from ``app_root/data`` and ``app_root/secret``."""
    global _runtime

    person = resolve_person_short(person_short)
    profile_path = project_path("secret", f"{person}_profile.json")
    if not profile_path.is_file():
        raise FileNotFoundError(f"Profile not found: {profile_path}")

    profile_person = str(json.loads(profile_path.read_text(encoding="utf-8")).get("person") or "").strip()
    if profile_person != person:
        raise ValueError(
            f"{profile_path.name} has person {profile_person!r}, expected {person!r}."
        )

    _runtime = AppSettings(
        person_short=person,
        profile_path=profile_path,
        private_key_path=resolve_private_key_path(),
        data_dir=data_dir().resolve(),
        server_url=os.environ.get("bankingApp_SERVER_URL", "").strip(),
        server_api_key=os.environ.get("bankingApp_SERVER_API_KEY", "").strip(),
    )
    return _runtime
