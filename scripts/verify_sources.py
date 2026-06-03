"""Phase 0 — data-source verification harness (zero third-party deps).

Purpose: BEFORE building anything on top of the connectors, prove that each
data source actually answers with real credentials. This is the single most
important de-risking step (see docs/architecture.md §4: "リスク先行").

It reads keys from `.env` (copy from `.env.example` first) and, for each
source that has credentials, makes ONE real authenticated request and reports
PASS / SKIP (no key) / FAIL (with the HTTP status and a short reason).

Run:
    py scripts/verify_sources.py

Design note: this harness prints the *actual* HTTP status rather than assuming
success, because its whole job is to discover reality — including the exact
USPTO ODP endpoint/header, which official docs render via JS and are easiest to
confirm against a live 200.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")

# --- Endpoints (EPO confirmed; USPTO base/header to confirm on first live run) ---
EPO_TOKEN_URL = "https://ops.epo.org/3.2/auth/accesstoken"
EPO_BIBLIO_URL = "https://ops.epo.org/3.2/rest-services/published-data/publication/epodoc/EP1000000/biblio"

# USPTO ODP: confirm against https://data.uspto.gov/apis/getting-started during Phase 0.
USPTO_BASE = os.environ.get("USPTO_ODP_BASE", "https://api.uspto.gov")
USPTO_SMOKE_PATH = os.environ.get("USPTO_ODP_PATH", "/api/v1/patent/applications/search?q=*&limit=1")
USPTO_API_KEY_HEADER = os.environ.get("USPTO_ODP_HEADER", "X-API-KEY")


def load_dotenv(path: str) -> dict:
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _status(label: str, ok: bool, detail: str) -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}: {detail}")
    return ok


def _skip(label: str, detail: str) -> None:
    print(f"[SKIP] {label}: {detail}")


def check_uspto(api_key: str) -> bool:
    if not api_key:
        _skip("USPTO ODP", "no USPTO_ODP_API_KEY in .env — register at "
              "https://data.uspto.gov/apis/getting-started")
        return True  # not a failure; just not configured yet
    url = USPTO_BASE + USPTO_SMOKE_PATH
    req = urllib.request.Request(url, headers={USPTO_API_KEY_HEADER: api_key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _status("USPTO ODP", resp.status == 200, f"HTTP {resp.status} from {url}")
    except urllib.error.HTTPError as e:
        return _status("USPTO ODP", False,
                       f"HTTP {e.code} from {url} — confirm endpoint/header at getting-started docs")
    except Exception as e:  # noqa: BLE001
        return _status("USPTO ODP", False, f"{type(e).__name__}: {e}")


def check_epo(key: str, secret: str) -> bool:
    if not key or not secret:
        _skip("EPO OPS", "no EPO_OPS_KEY/SECRET in .env — register at https://developers.epo.org/")
        return True
    # 1) OAuth2 client_credentials -> access token
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        EPO_TOKEN_URL, data=data,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            token = json.loads(resp.read()).get("access_token")
    except urllib.error.HTTPError as e:
        return _status("EPO OPS (auth)", False, f"HTTP {e.code} obtaining token — check key/secret")
    except Exception as e:  # noqa: BLE001
        return _status("EPO OPS (auth)", False, f"{type(e).__name__}: {e}")
    if not token:
        return _status("EPO OPS (auth)", False, "no access_token in response")
    _status("EPO OPS (auth)", True, "access token obtained")

    # 2) one bibliographic request
    req = urllib.request.Request(EPO_BIBLIO_URL, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _status("EPO OPS (biblio)", resp.status == 200,
                           f"HTTP {resp.status} for EP1000000/biblio")
    except urllib.error.HTTPError as e:
        return _status("EPO OPS (biblio)", False, f"HTTP {e.code} for EP1000000/biblio")
    except Exception as e:  # noqa: BLE001
        return _status("EPO OPS (biblio)", False, f"{type(e).__name__}: {e}")


def main() -> int:
    env = {**load_dotenv(os.path.join(ROOT, ".env")), **os.environ}
    print("=== Phase 0: data-source verification ===\n")
    results = [
        check_uspto(env.get("USPTO_ODP_API_KEY", "")),
        check_epo(env.get("EPO_OPS_KEY", ""), env.get("EPO_OPS_SECRET", "")),
    ]
    print()
    if all(results):
        print("All configured sources OK (un-configured sources were skipped).")
        return 0
    print("One or more configured sources FAILED — see details above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
