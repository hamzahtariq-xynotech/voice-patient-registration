#!/usr/bin/env python
"""Create or update the Vapi tools (and optionally the assistant) from this repo.

Hand-entering create_patient's 16 parameters in the dashboard is slow and easy to
typo, so this pushes prompts/vapi_tools.json straight to the Vapi API.

It is idempotent: tools are matched by function name and assistants by name, so
re-running updates in place instead of creating duplicates. That makes the common
follow-up a one-liner -- when your public URL changes (ngrok -> Railway), re-run
with the new --server-url and every tool is repointed.

The API key is read from .env (VAPI_API_KEY), the environment, or --api-key,
in that order of convenience -- .env is gitignored, so the key never lands in a
commit.

Usage
-----
  # .env:  VAPI_API_KEY=your-private-key
  uv run python scripts/vapi_setup.py --server-url https://your-app.up.railway.app

  # preview without sending anything
  uv run python scripts/vapi_setup.py --server-url https://x.ngrok.io --dry-run

  # tools only, leave the assistant alone
  uv run python scripts/vapi_setup.py --server-url https://x.ngrok.io --skip-assistant

  # if you set VAPI_WEBHOOK_SECRET on the server, mirror it onto the tools
  uv run python scripts/vapi_setup.py --server-url https://x --secret s3cret
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_ROOT = "https://api.vapi.ai"
REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_FILE = REPO_ROOT / "prompts" / "vapi_tools.json"
PROMPT_FILE = REPO_ROOT / "prompts" / "system_prompt.md"
ENV_FILE = REPO_ROOT / ".env"


def load_env() -> bool:
    """Load .env from the repo root, whatever directory the script is run from.

    Falls back to a minimal parser so the script still works under a bare
    `python` outside the uv environment.
    """
    if not ENV_FILE.exists():
        return False
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_FILE)
    except ImportError:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    return True

ASSISTANT_NAME = "Patient Intake"
TOOL_NAMES = ("check_existing_patient", "create_patient", "update_patient")


# --------------------------------------------------------------------------- #
# tiny HTTP client (stdlib only, so the script runs with no extra deps)
# --------------------------------------------------------------------------- #

class ApiError(RuntimeError):
    def __init__(self, status: int, body: str, method: str, path: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"{method} {path} -> HTTP {status}\n{body}")


def request(method: str, path: str, api_key: str, payload: dict | None = None) -> Any:
    url = f"{API_ROOT}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            body = res.read().decode()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, exc.read().decode(errors="replace"), method, path) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from None


# --------------------------------------------------------------------------- #
# building payloads from the files in this repo
# --------------------------------------------------------------------------- #

def load_tool_payloads(base_url: str, secret: str | None) -> list[dict]:
    """Read vapi_tools.json and point every tool at <base_url>/vapi/tools."""
    spec = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    payloads = []
    for tool in spec["tools"]:
        server: dict[str, Any] = {"url": f"{base_url}/vapi/tools", "timeoutSeconds": 20}
        if secret:
            # Matches the VAPI_WEBHOOK_SECRET check in app/routers/vapi.py.
            server["headers"] = {"x-vapi-secret": secret}
        payloads.append(
            {
                "type": "function",
                "function": tool["function"],
                "server": server,
                "messages": tool.get("messages", []),
            }
        )
    return payloads


def extract_prompt_and_greeting() -> tuple[str, str]:
    """Pull the clean prompt and first message out of prompts/system_prompt.md.

    The file keeps an annotated copy for humans and a fenced clean copy for the
    assistant; only the clean one may reach the model.
    """
    text = PROMPT_FILE.read_text(encoding="utf-8")

    def fenced_after(heading: str) -> str:
        idx = text.find(heading)
        if idx == -1:
            raise RuntimeError(f"Could not find section {heading!r} in {PROMPT_FILE.name}")
        match = re.search(r"```\n(.*?)```", text[idx:], re.DOTALL)
        if not match:
            raise RuntimeError(f"No fenced block under {heading!r} in {PROMPT_FILE.name}")
        return match.group(1).strip()

    prompt = fenced_after("## Clean prompt (paste this into Vapi)")
    greeting = fenced_after("## First message")
    if "<!--" in prompt:
        raise RuntimeError("Refusing to upload a prompt containing HTML comments")
    return prompt, greeting


def build_assistant_payload(base_url: str, tool_ids: list[str], voice: str | None) -> dict:
    prompt, greeting = extract_prompt_and_greeting()
    payload: dict[str, Any] = {
        "name": ASSISTANT_NAME,
        "firstMessage": greeting,
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "temperature": 0.4,
            "messages": [{"role": "system", "content": prompt}],
            "toolIds": tool_ids,
        },
        "server": {"url": f"{base_url}/vapi/events"},
        "serverMessages": ["end-of-call-report"],
        "endCallFunctionEnabled": True,
        "maxDurationSeconds": 900,
        "silenceTimeoutSeconds": 20,
    }
    if voice:
        payload["voice"] = {"provider": "vapi", "voiceId": voice}
    return payload


# --------------------------------------------------------------------------- #
# sync logic
# --------------------------------------------------------------------------- #

def sync_tools(api_key: str, payloads: list[dict], dry_run: bool) -> dict[str, str]:
    existing = {}
    if not dry_run:
        for tool in request("GET", "/tool?limit=1000", api_key) or []:
            name = (tool.get("function") or {}).get("name")
            if name in TOOL_NAMES:
                existing[name] = tool["id"]

    ids: dict[str, str] = {}
    for payload in payloads:
        name = payload["function"]["name"]
        n_params = len(payload["function"].get("parameters", {}).get("properties", {}))

        if dry_run:
            print(f"  [dry-run] would send {name} ({n_params} parameters)")
            print(json.dumps(payload, indent=2)[:600] + "\n  ...")
            continue

        if name in existing:
            tool = request("PATCH", f"/tool/{existing[name]}", api_key, payload)
            action = "updated"
        else:
            tool = request("POST", "/tool", api_key, payload)
            action = "created"
        ids[name] = tool["id"]
        print(f"  {action:>7}  {name:<24} {n_params:>2} params  id={tool['id']}")
    return ids


def sync_assistant(api_key: str, payload: dict, dry_run: bool) -> str | None:
    if dry_run:
        print("  [dry-run] assistant payload:")
        print(json.dumps({**payload, "model": {**payload["model"], "messages": ["<prompt>"]}}, indent=2))
        return None

    existing = None
    for assistant in request("GET", "/assistant?limit=1000", api_key) or []:
        if assistant.get("name") == ASSISTANT_NAME:
            existing = assistant["id"]
            break

    if existing:
        assistant = request("PATCH", f"/assistant/{existing}", api_key, payload)
        print(f"  updated  assistant {ASSISTANT_NAME!r}  id={assistant['id']}")
    else:
        assistant = request("POST", "/assistant", api_key, payload)
        print(f"  created  assistant {ASSISTANT_NAME!r}  id={assistant['id']}")
    return assistant["id"]


# --------------------------------------------------------------------------- #

def mask(secret: str) -> str:
    """Show just enough of a key to confirm the right one loaded."""
    # ASCII only -- the Windows console mangles non-ASCII in this context.
    return f"{secret[:4]}...{secret[-4:]}" if len(secret) > 12 else "set"


def main() -> int:
    # Must run before argparse, which reads these as defaults.
    env_loaded = load_env()

    parser = argparse.ArgumentParser(description="Sync Vapi tools/assistant from this repo.")
    parser.add_argument(
        "--server-url",
        required=True,
        help="Public base URL of the deployed API, e.g. https://app.up.railway.app",
    )
    parser.add_argument("--api-key", default=os.environ.get("VAPI_API_KEY", ""),
                        help="Defaults to VAPI_API_KEY from .env or the environment")
    parser.add_argument("--secret", default=os.environ.get("VAPI_WEBHOOK_SECRET", ""),
                        help="Mirror of VAPI_WEBHOOK_SECRET; sent as the x-vapi-secret header")
    parser.add_argument("--voice", default="Elliot", help="Vapi voice id (--voice '' to omit)")
    parser.add_argument("--skip-assistant", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base_url = args.server_url.rstrip("/")
    if not base_url.startswith("https://") and "localhost" not in base_url:
        print("! Vapi requires a public HTTPS URL for webhooks.", file=sys.stderr)
        return 2
    if not args.api_key and not args.dry_run:
        where = f"{ENV_FILE.name} has no VAPI_API_KEY" if env_loaded else "no .env found"
        print(
            f"! No API key ({where}).\n"
            "  Add VAPI_API_KEY=... to .env, or pass --api-key.\n"
            "  Use the PRIVATE key from Dashboard -> API Keys; the public key gives 403.",
            file=sys.stderr,
        )
        return 2

    print(f"\nTarget: {base_url}")
    print(f"  tools  -> {base_url}/vapi/tools")
    print(f"  events -> {base_url}/vapi/events")
    print(f"  api key: {mask(args.api_key) if args.api_key else 'none (dry run)'}"
          f"{' from .env' if env_loaded and args.api_key else ''}")
    print(f"  secret header: {'yes' if args.secret else 'no'}\n")

    try:
        print("Tools")
        tool_ids = sync_tools(
            args.api_key, load_tool_payloads(base_url, args.secret or None), args.dry_run
        )

        if args.skip_assistant:
            print("\nSkipping assistant (--skip-assistant).")
            return 0

        print("\nAssistant")
        ordered_ids = [tool_ids[n] for n in TOOL_NAMES if n in tool_ids]
        payload = build_assistant_payload(base_url, ordered_ids, args.voice or None)
        try:
            sync_assistant(args.api_key, payload, args.dry_run)
        except ApiError as exc:
            # Tools are the tedious part and they already succeeded -- don't lose that.
            print(f"\n! Assistant sync failed:\n{exc}\n", file=sys.stderr)
            print(
                "  The tools were created successfully. Build the assistant in the\n"
                "  dashboard instead (Assistants -> Create), attach the three tools\n"
                "  listed above, and paste the prompt from prompts/system_prompt.md.\n"
                "  See README section 7 for the remaining fields.",
                file=sys.stderr,
            )
            return 1
    except ApiError as exc:
        print(f"\n! {exc}", file=sys.stderr)
        return 1
    except (RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"\n! {exc}", file=sys.stderr)
        return 1

    if not args.dry_run:
        print("\nDone. Remaining manual step: Phone Numbers -> Create Phone Number ->")
        print(f"Free Vapi Number, then set Inbound to the {ASSISTANT_NAME!r} assistant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
