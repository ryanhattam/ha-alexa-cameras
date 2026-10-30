#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Tom Hirt
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""How to reach Home Assistant — the one place that knows which mode we're in.

This project runs two ways, and the only difference between them is how it talks to
Home Assistant:

  **Add-on** (Home Assistant OS / Supervised). The Supervisor injects a token and
  proxies the core API, so it lives at http://supervisor/core/api/... and needs no
  URL or credentials from the user. `homeassistant_api: true` in config.yaml grants it.

  **Standalone container** (plain Docker, Unraid, …). There is no Supervisor and no
  token, so the user supplies both: HA_URL (e.g. http://192.168.1.100:8123) and
  HA_TOKEN (a long-lived access token from Home Assistant -> your profile -> Security).

Everything Home-Assistant-shaped goes through here, so neither ui.py nor injector.py
has to care which mode it is in. There are only two calls in the whole project:
GET /api/states (the TTS engine dropdown) and POST /api/tts_get_url (rendering an
announcement). Both are optional features that degrade gracefully, so a standalone
install with no token still serves cameras and still injects `{"url":...}` audio.

`env()` is the keystone, and the reason this module exists rather than a few
os.environ lookups: s6-overlay keeps the *container* environment out of the service
environment and dumps it as one file per variable under /run/s6/container_environment.
That is why run.sh has always read TZ from there, and SUPERVISOR_TOKEN too. The same
applies to HA_URL / HA_TOKEN from `docker run -e` or docker-compose, so reading
os.environ alone would silently see nothing. env() tries the s6 file first and falls
back to the real environment for a non-s6 run.

Precedence is **environment > config file > default**: HA_URL / HA_TOKEN are the
deployment's own settings (docker-compose.yml, the Unraid template), while `ha_base` /
`ha_token` in /data/config.yaml are the per-install override. As an add-on nothing sets
the environment, so `ha_base` keeps behaving exactly as it always has.

Standard library + PyYAML only, matching the rest of the project.
"""
import json
import os
import urllib.request

import yaml

CONFIG = "/data/config.yaml"

# s6-overlay keeps the container env out of the service env but persists it as one
# file per variable here. See the module docstring.
S6_ENV_DIR = "/run/s6/container_environment"

# Add-on mode: the Supervisor proxies the core API, authenticated with its own token.
ADDON_API_BASE = "http://supervisor/core"
# Home Assistant's address on the add-on network. Also the fallback origin for fetching
# rendered TTS audio, which dodges the hairpin-NAT flakiness of HA's external LAN URL.
ADDON_HA_ORIGIN = "http://homeassistant:8123"

_ENV_CACHE = {}


def env(name):
    """A container environment variable, s6-aware ("" if unset).

    Cached: the container environment cannot change while we run.
    """
    if name not in _ENV_CACHE:
        try:
            val = open(os.path.join(S6_ENV_DIR, name)).read().strip()
        except Exception:
            val = os.environ.get(name, "").strip()
        _ENV_CACHE[name] = val
    return _ENV_CACHE[name]


def _config(key):
    """One top-level key from the self-managed config, or "" if unreadable/unset.

    Read fresh each time rather than cached: the Web UI rewrites this file whenever
    the user saves, and these calls are rare (a dropdown load, an announcement).

    A hand-edited file whose root is not a mapping is a real possibility (the UI
    rejects one, but nothing stops an editor), so it degrades to "" like any other
    unreadable config rather than raising into a caller that only wants a string.
    """
    try:
        cfg = yaml.safe_load(open(CONFIG))
    except Exception:
        return ""
    if not isinstance(cfg, dict):
        return ""
    return str(cfg.get(key, "") or "").strip()


def supervisor_token():
    """The Supervisor's token, or "" when not running as an add-on."""
    return env("SUPERVISOR_TOKEN")


def is_addon():
    """True when running as a Home Assistant add-on.

    The presence of a Supervisor token *is* the test — it's the thing that actually
    determines which API path works, so there's nothing to misconfigure. STANDALONE=1
    forces the standalone path anyway, which is handy for testing.
    """
    if env("STANDALONE").lower() in ("1", "true", "yes", "on"):
        return False
    return bool(supervisor_token())


def mode():
    """"addon" or "standalone" — for the startup diagnostics and the UI."""
    return "addon" if is_addon() else "standalone"


def token():
    """Bearer token for the Home Assistant API ("" if none is available)."""
    if is_addon():
        return supervisor_token()
    return env("HA_TOKEN") or _config("ha_token")


def ha_origin():
    """Where Home Assistant itself lives — scheme://host:port, no trailing slash.

    Used directly to fetch HA-rendered TTS audio, and as the API base when there is no
    Supervisor to proxy for us. As an add-on this resolves to the internal hostname
    unless `ha_base` overrides it, which is the long-standing behaviour.
    """
    return (env("HA_URL") or _config("ha_base") or ADDON_HA_ORIGIN).rstrip("/")


def api_base():
    """Base URL for Home Assistant's REST API — the part before "/api/...".

    Add-on mode always goes through the Supervisor's proxy; `ha_base` does not apply
    to it, since that only ever described where to fetch *audio* from.
    """
    return ADDON_API_BASE if is_addon() else ha_origin()


def _request(path, data=None):
    """Build an authenticated Request, with actionable errors for what's missing.

    These messages surface in the UI's TTS dropdown and in /say's reply, so they name
    the setting to fix rather than just failing with a 401 later.
    """
    tok = token()
    if not tok:
        raise RuntimeError(
            "no Home Assistant token — set HA_TOKEN to a long-lived access token "
            "(Home Assistant -> your profile -> Security -> Long-lived access tokens), "
            "or 'ha_token' in the config. As an add-on the Supervisor provides this "
            "automatically; if you see this there, restart the add-on.")
    if not env("HA_URL") and not _config("ha_base") and not is_addon():
        raise RuntimeError(
            "no Home Assistant URL — set HA_URL (e.g. http://192.168.1.100:8123) on "
            "the container, or 'ha_base' in the config. The default internal hostname "
            "only resolves on the Home Assistant add-on network.")
    headers = {"Authorization": "Bearer " + tok}
    if data is not None:
        headers["Content-Type"] = "application/json"
    return urllib.request.Request(api_base() + path, data=data, headers=headers)


def ha_get(path, timeout=8):
    """GET a Home Assistant API path (e.g. "/api/states") -> parsed JSON."""
    return json.load(urllib.request.urlopen(_request(path), timeout=timeout))


def ha_post(path, payload, timeout=15):
    """POST JSON to a Home Assistant API path -> parsed JSON."""
    body = json.dumps(payload).encode()
    return json.load(urllib.request.urlopen(_request(path, body), timeout=timeout))
