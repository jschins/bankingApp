"""Command-line interface for downloading raw PSD2 transaction JSON via Enable Banking.

Usage (after `pip install -e .` and filling in .env):

  # 1. List banks for your country and find the ASPSP name (ING NL = "ING")
  python -m psd2_api aspsps

  # 2. Create an authentication link and open the printed URL in your browser
  python -m psd2_api link --aspsp ING

  # 3. After authenticating in your ING app you land on the redirect URL with
  #    a ?code=... parameter. Pass that code to create a session:
  python -m psd2_api session --code <CODE_FROM_REDIRECT_URL>

  # 4. (optional) Show the linked accounts
  python -m psd2_api status

  # 5. Download raw transactions JSON for every linked account
  python -m psd2_api fetch
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from .client import EnableBankingClient, EnableBankingError
from .profile import list_profiles, load_profile
from .uploader import bankingAppServerClient, ServerError

LOCAL_CONSENT_FILE = Path("consent.json")
DEFAULT_OUTPUT_DIR = Path("data")
# Name of the small consent handle stored per person on bankingApp-server.
CONSENT_NAME = "consent.json"


def _client(person: str | None = None) -> EnableBankingClient:
    load_dotenv()
    if person:
        profile = load_profile(person)
        if not profile.app_id or not str(profile.key_path):
            raise EnableBankingError(
                f"No application credentials for person {person!r} "
                f"(expected packaging/profiles/{person}/profile.json + its .pem)."
            )
        return EnableBankingClient.from_key_file(profile.app_id, str(profile.key_path))
    return EnableBankingClient.from_key_file(
        application_id=os.environ.get("ENABLEBANKING_APP_ID", ""),
        key_path=os.environ.get("ENABLEBANKING_KEY_PATH", ""),
    )


def _load_local_consent() -> dict:
    if LOCAL_CONSENT_FILE.exists():
        return json.loads(LOCAL_CONSENT_FILE.read_text(encoding="utf-8"))
    return {}


def _save_local_consent(record: dict) -> None:
    LOCAL_CONSENT_FILE.write_text(json.dumps(record, indent=2), encoding="utf-8")


def _resolve_consent(person: str | None) -> tuple[dict | None, str | None]:
    """Return a consent record and the person id to use for API credentials."""
    if person:
        profile = load_profile(person)
        server = bankingAppServerClient(profile.server_url, profile.server_api_key)
        return server.get_json(person, CONSENT_NAME), person
    record = _load_local_consent()
    if not record:
        return None, None
    return record, record.get("person")


def _dump_json(data: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote {path}")


def _extract_code(code_or_url: str) -> str:
    """Accept either a raw code or the full redirect URL and return the code."""
    if code_or_url.startswith("http"):
        qs = parse_qs(urlparse(code_or_url).query)
        codes = qs.get("code")
        if not codes:
            raise EnableBankingError(f"No 'code' parameter found in URL: {code_or_url}")
        return codes[0]
    return code_or_url


def cmd_aspsps(args: argparse.Namespace) -> int:
    country = args.country or os.environ.get("PSD2_COUNTRY", "NL")
    aspsps = _client().list_aspsps(country)
    for aspsp in aspsps:
        print(f"{aspsp.get('name'):<28} {aspsp.get('country')}")
    print(f"\n{len(aspsps)} ASPSPs for country '{country}'.")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    country = args.country or os.environ.get("PSD2_COUNTRY", "NL")
    aspsp = args.aspsp or os.environ.get("PSD2_ASPSP", "ING")
    redirect = args.redirect or os.environ.get("PSD2_REDIRECT_URL", "http://localhost:8000/")
    valid_until = (
        datetime.now(timezone.utc) + timedelta(days=args.valid_days)
    ).isoformat()

    client = _client()
    result = client.start_authorization(
        aspsp_name=aspsp, country=country, redirect_url=redirect, valid_until=valid_until
    )

    print("Open this URL in your browser and authenticate with your bank:\n")
    print(f"  {result['url']}\n")
    print("After authenticating you will be redirected to your redirect URL with")
    print("a `?code=...` parameter. Copy that code (or the whole URL) and run:\n")
    print("  python -m psd2_api session --code <CODE>")
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    code = _extract_code(args.code)
    client = _client()
    session = client.create_session(code)

    accounts = session.get("accounts", [])
    country = os.environ.get("PSD2_COUNTRY", "NL")
    aspsp = os.environ.get("PSD2_ASPSP", "ING")
    person = os.environ.get("PSD2_PERSON", "local")
    record = _consent_from_session(
        person=person,
        aspsp=aspsp,
        country=country,
        session=session,
    )
    _save_local_consent(record)

    print(f"Session created: {session.get('session_id')}")
    print(f"Linked accounts: {len(accounts)}")
    for acc in record["accounts"]:
        print(f"  - {acc['uid']}  {acc.get('iban') or ''}  {acc.get('name') or ''}")
    print(f"\nSaved consent to {LOCAL_CONSENT_FILE}.")
    print("Now run:  python -m psd2_api fetch")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    record = _load_local_consent()
    session_id = record.get("session_id")
    if not session_id:
        print("No consent yet. Run `link` then `session --code <CODE>` first.", file=sys.stderr)
        return 1
    session = _client().get_session(session_id)
    print(f"Session: {session_id}")
    print(f"Access valid until: {record.get('valid_until') or (session.get('access') or {}).get('valid_until')}")
    accounts = record.get("accounts", [])
    print(f"Accounts: {len(accounts)}")
    for acc in accounts:
        print(f"  - {acc.get('uid')}  {acc.get('iban') or ''}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    try:
        record, cred_person = _resolve_consent(args.person)
    except ServerError as exc:
        print(f"Server error: {exc}", file=sys.stderr)
        return 1

    accounts = (record or {}).get("accounts", [])
    if not accounts:
        print(
            "No linked accounts. Run `fetch --person <p>` (reads consent from bankingApp-server), "
            "`pull-consent --person <p>`, or `link` then `session --code <CODE>` first.",
            file=sys.stderr,
        )
        return 1

    person = args.person or cred_person
    if person and person != "local":
        client = _client(person)
    else:
        client = _client()
    out_dir = Path(args.output_dir or DEFAULT_OUTPUT_DIR)

    for acc in accounts:
        uid = acc["uid"]
        label = acc.get("iban") or uid
        print(f"Account {label} ({uid}):")
        transactions = client.get_transactions(
            uid, date_from=args.date_from, date_to=args.date_to
        )
        print(f"  {len(transactions)} transactions")
        _dump_json(transactions, out_dir / f"transactions_{uid}.json")

        if not args.transactions_only:
            _dump_json(client.get_account_details(uid), out_dir / f"details_{uid}.json")
            _dump_json(client.get_balances(uid), out_dir / f"balances_{uid}.json")

    print("\nDone.")
    return 0


def _consent_from_session(
    *,
    person: str,
    aspsp: str,
    country: str,
    session: dict,
    valid_until: str | None = None,
) -> dict:
    if not valid_until:
        valid_until = (session.get("access") or {}).get("valid_until") or (
            datetime.now(timezone.utc) + timedelta(days=90)
        ).isoformat()
    return {
        "person": person,
        "aspsp": aspsp,
        "country": country,
        "session_id": session.get("session_id"),
        "valid_until": valid_until,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "accounts": [
            {
                "uid": acc.get("uid"),
                "iban": (acc.get("account_id") or {}).get("iban"),
                "identification_hash": acc.get("identification_hash"),
                "name": acc.get("name"),
                "currency": acc.get("currency"),
            }
            for acc in session.get("accounts", [])
        ],
    }


def _build_consent_record(profile, session: dict, valid_until: str) -> dict:
    """Assemble the small consent handle that travels to the admin via the server.

    It carries no transactions — only what the admin needs to fetch the data
    themselves: the (per-session) account ``uid``, the cross-session-stable
    ``identification_hash``/IBAN, and how long the consent is valid.
    """
    return _consent_from_session(
        person=profile.person,
        aspsp=profile.aspsp,
        country=profile.country,
        session=session,
        valid_until=valid_until,
    )


def cmd_consent(args: argparse.Namespace) -> int:
    """Guided re-authorization for a family member (the packaged executable).

    Wraps link -> (browser SCA in the bank app) -> session, then reports a small
    consent record to the server. No transactions are fetched or uploaded here;
    the administrator pulls the record and fetches the data themselves.
    """
    profile = load_profile()
    missing = profile.missing()
    if missing:
        print("This build is misconfigured; missing: " + ", ".join(missing), file=sys.stderr)
        return 2

    try:
        client = EnableBankingClient.from_key_file(profile.app_id, str(profile.key_path))
    except EnableBankingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Hello! This will refresh banking access for: {profile.person}")
    print(f"Bank: {profile.aspsp} ({profile.country})\n")

    valid_until = (
        datetime.now(timezone.utc) + timedelta(days=args.valid_days)
    ).isoformat()
    auth = client.start_authorization(
        aspsp_name=profile.aspsp,
        country=profile.country,
        redirect_url=profile.redirect_url,
        valid_until=valid_until,
    )
    url = auth.get("url", "")
    print("Step 1 - A browser will open. Log in and approve access in your bank app.")
    print("If it does not open automatically, copy this link into your browser:\n")
    print(f"  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - opening a browser is best-effort
        pass

    print("Step 2 - After approving, your browser lands on a page whose address")
    print("starts with the redirect URL and contains '?code=...'.")
    pasted = input("Paste that full address here and press Enter:\n> ").strip()
    if not pasted:
        print("No URL entered; aborting.", file=sys.stderr)
        return 1

    session = client.create_session(_extract_code(pasted))
    accounts = session.get("accounts", [])
    if not accounts:
        print("No accounts were linked. Please try again.", file=sys.stderr)
        return 1

    print(f"\nLinked {len(accounts)} account(s):")
    for acc in accounts:
        iban = (acc.get("account_id") or {}).get("iban") or ""
        print(f"  - {iban} {acc.get('name') or ''}")

    record = _build_consent_record(profile, session, valid_until)

    # Send only the consent handle to the server; fall back to a local file so
    # the family member can forward it manually if the server is unreachable.
    try:
        server = bankingAppServerClient(profile.server_url, profile.server_api_key)
        server.put_json(profile.person, CONSENT_NAME, record)
    except ServerError as exc:
        fallback = Path(f"{profile.person}_{CONSENT_NAME}").resolve()
        fallback.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"\nAccess was refreshed, but the server could not be reached:\n  {exc}",
              file=sys.stderr)
        print(f"A file was saved at:\n  {fallback}\nPlease send this file to the administrator.")
        return 1

    print("\nDone! Access has been refreshed and reported to the administrator.")
    print(f"Valid until: {valid_until}")
    return 0


def cmd_pull_consent(args: argparse.Namespace) -> int:
    """Admin: download a person's consent record from the server to consent.json.

    After this, run ``fetch`` (with that person's application credentials) to
    pull their transactions using the freshly-authorized account uids. You can
    also skip this step and pass ``--person`` directly to ``fetch``, which reads
    consent from bankingApp-server.
    """
    profile = load_profile(args.person)
    person = args.person or profile.person
    if not person:
        print("Specify --person (or set it in the profile / PSD2_PERSON).", file=sys.stderr)
        return 1

    server = bankingAppServerClient(profile.server_url, profile.server_api_key)
    record = server.get_json(person, CONSENT_NAME)
    if not record:
        print(f"No consent record found for {person!r} on the server.", file=sys.stderr)
        return 1

    accounts = record.get("accounts", [])
    _save_local_consent(record)

    print(f"Consent for {person!r} (valid until {record.get('valid_until')}):")
    for acc in accounts:
        print(f"  - {acc.get('uid')}  {acc.get('iban') or ''}  {acc.get('name') or ''}")
    print(
        f"\nSaved to {LOCAL_CONSENT_FILE}.\n"
        "Now run:  psd2api fetch --date-from <YYYY-MM-DD>"
    )
    return 0


def _is_expired(valid_until: str | None) -> bool:
    """True if an ISO ``valid_until`` is in the past (unknown/blank → not expired)."""
    if not valid_until:
        return False
    try:
        dt = datetime.fromisoformat(valid_until)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < datetime.now(timezone.utc)


def _collect_one(
    person: str,
    date_from: str | None,
    date_to: str | None,
    output_dir: Path,
) -> dict[str, Any]:
    """Fetch one person's bank data and write it under ``output_dir/<person>/``.

    Reads the person's consent record from bankingApp-server (the only thing the
    server holds), then fetches and writes
    ``transactions_<uid>``/``details_<uid>``/``balances_<uid>`` straight to the
    target directory (bankingApp-admin's storage). The raw bank data never travels
    through bankingApp-server. Returns a status dict (never raises) so a batch can
    continue.
    """
    result: dict[str, Any] = {
        "person": person,
        "status": "error",
        "accounts": 0,
        "transactions": 0,
        "error": None,
    }
    profile = load_profile(person)
    if not profile.app_id or not str(profile.key_path):
        result["status"] = "no-credentials"
        return result
    try:
        server = bankingAppServerClient(profile.server_url, profile.server_api_key)
        record = server.get_json(person, CONSENT_NAME)
    except ServerError as exc:
        result["error"] = str(exc)
        return result
    if not record:
        result["status"] = "no-consent"
        return result
    if _is_expired(record.get("valid_until")):
        result["status"] = "consent-expired"
        return result

    accounts = record.get("accounts", []) or []
    dest = output_dir / person
    try:
        client = EnableBankingClient.from_key_file(profile.app_id, str(profile.key_path))
        total_tx = 0
        for acc in accounts:
            uid = acc.get("uid")
            if not uid:
                continue
            transactions = client.get_transactions(
                uid, date_from=date_from, date_to=date_to
            )
            _dump_json(transactions, dest / f"transactions_{uid}.json")
            _dump_json(client.get_account_details(uid), dest / f"details_{uid}.json")
            _dump_json(client.get_balances(uid), dest / f"balances_{uid}.json")
            total_tx += len(transactions)
    except (EnableBankingError, ServerError) as exc:
        result["error"] = str(exc)
        return result

    result["status"] = "ok"
    result["accounts"] = len(accounts)
    result["transactions"] = total_tx
    return result


def cmd_collect(args: argparse.Namespace) -> int:
    """Fetch bank data for one or all people and write it to a target directory.

    This is the admin's one-shot collector: for each person it reads their
    consent record from bankingApp-server, fetches transactions with their own
    credentials, and writes the raw files under ``--output-dir/<person>/``
    (bankingApp-admin's storage). People with no/expired consent are skipped and
    reported (re-run their executable to refresh access).
    """
    if args.all:
        persons = list_profiles()
    elif args.person:
        persons = [args.person]
    else:
        print("Specify --person <p> or --all.", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    results = [
        _collect_one(p, args.date_from, args.date_to, output_dir) for p in persons
    ]

    if args.json:
        print(json.dumps({"results": results}))
    else:
        for r in results:
            line = f"{r['person']}: {r['status']}"
            if r["status"] == "ok":
                line += f" ({r['accounts']} account(s), {r['transactions']} transactions)"
            if r.get("error"):
                line += f" - {r['error']}"
            print(line)

    # Hard failure only if there were people and every one errored outright.
    statuses = {r["status"] for r in results}
    if results and statuses == {"error"}:
        return 1
    return 0


def _detect_lan_ip() -> str | None:
    """Best-effort LAN IPv4 of this machine (no traffic is actually sent)."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connecting a UDP socket just selects the outbound interface.
        sock.connect(("192.168.1.1", 9))
        ip = sock.getsockname()[0]
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    finally:
        sock.close()
    if not ip or ip.startswith("127.") or ip.startswith("169.254."):
        return None
    return ip


def cmd_server_url(args: argparse.Namespace) -> int:
    """Print the bankingApp-server base URL for this machine (to fill server.json).

    Run this on the machine that hosts bankingApp-server. The hostname form is
    preferred for server.json because it survives DHCP IP changes; the IP form
    is shown as a fallback for networks where name resolution is unavailable.
    """
    import socket

    port = args.port
    host = socket.gethostname()
    print(f"hostname : http://{host}:{port}")
    ip = _detect_lan_ip()
    if ip:
        print(f"lan ip   : http://{ip}:{port}")
    else:
        print("lan ip   : (could not detect a LAN IPv4 address)")
    print(
        "\nPut the hostname URL in packaging/server.json (\"server_url\"); it keeps\n"
        "working when the IP changes, as long as clients are on the same LAN."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="psd2api", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_aspsps = sub.add_parser("aspsps", help="List banks (ASPSPs) for a country")
    p_aspsps.add_argument("--country", help="ISO country code, e.g. NL (default from .env)")
    p_aspsps.set_defaults(func=cmd_aspsps)

    p_link = sub.add_parser("link", help="Create an authentication link for a bank")
    p_link.add_argument("--aspsp", help="ASPSP name, e.g. ING (default from .env or 'ING')")
    p_link.add_argument("--country", help="ISO country code, e.g. NL (default from .env)")
    p_link.add_argument("--redirect", help="Redirect URL (default from .env)")
    p_link.add_argument("--valid-days", type=int, default=90, help="Consent validity in days (default 90)")
    p_link.set_defaults(func=cmd_link)

    p_session = sub.add_parser("session", help="Create a session from the redirect code")
    p_session.add_argument("--code", required=True, help="The ?code=... value (or the full redirect URL)")
    p_session.set_defaults(func=cmd_session)

    p_status = sub.add_parser("status", help="Show session status and linked accounts")
    p_status.set_defaults(func=cmd_status)

    p_fetch = sub.add_parser("fetch", help="Download raw transactions JSON for linked accounts")
    p_fetch.add_argument(
        "--person",
        help="Use this person's app credentials and read their consent from "
        "bankingApp-server; without --person, uses local consent.json (from session "
        "or pull-consent)",
    )
    p_fetch.add_argument("--output-dir", help="Directory for JSON output (default: data/)")
    p_fetch.add_argument("--date-from", help="Start date YYYY-MM-DD (optional)")
    p_fetch.add_argument("--date-to", help="End date YYYY-MM-DD (optional)")
    p_fetch.add_argument(
        "--transactions-only",
        action="store_true",
        help="Skip account details and balances; only download transactions",
    )
    p_fetch.set_defaults(func=cmd_fetch)

    p_consent = sub.add_parser(
        "consent",
        help="Guided re-authorization; report the consent record (family member)",
    )
    p_consent.add_argument(
        "--valid-days", type=int, default=90, help="Consent validity in days (default 90)"
    )
    p_consent.set_defaults(func=cmd_consent)

    p_pull = sub.add_parser(
        "pull-consent",
        help="Admin: download a consent record to local consent.json",
    )
    p_pull.add_argument("--person", help="Person short (default from profile/PSD2_PERSON)")
    p_pull.set_defaults(func=cmd_pull_consent)

    p_collect = sub.add_parser(
        "collect",
        help="Admin: fetch bank data for one/all people into a target directory",
    )
    p_collect.add_argument("--person", help="Single person short (e.g. js)")
    p_collect.add_argument(
        "--all", action="store_true", help="All people under packaging/profiles"
    )
    p_collect.add_argument("--date-from", help="Start date YYYY-MM-DD (optional)")
    p_collect.add_argument("--date-to", help="End date YYYY-MM-DD (optional)")
    p_collect.add_argument(
        "--json", action="store_true", help="Emit a machine-readable JSON summary"
    )
    p_collect.add_argument(
        "--output-dir",
        help="Where to write raw files, as <dir>/<person>/ (default: data/)",
    )
    p_collect.set_defaults(func=cmd_collect)

    p_server_url = sub.add_parser(
        "server-url",
        help="Print this machine's bankingApp-server URL (run on the server host)",
    )
    p_server_url.add_argument(
        "--port", type=int, default=8000, help="bankingApp-server port (default 8000)"
    )
    p_server_url.set_defaults(func=cmd_server_url)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Load .env before any command reads environment variables.
    load_dotenv()
    if argv is None:
        argv = sys.argv[1:]
    # A double-clicked packaged executable starts with no arguments: run the
    # guided re-authorization flow that family members are meant to use.
    if not argv and getattr(sys, "frozen", False):
        argv = ["consent"]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except EnableBankingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ServerError as exc:
        print(f"Server error: {exc}", file=sys.stderr)
        return 1
    finally:
        # Keep the console window open so a non-technical user can read the
        # result before it closes (only when running as a packaged .exe).
        if getattr(sys, "frozen", False):
            try:
                input("\nPress Enter to close...")
            except EOFError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
