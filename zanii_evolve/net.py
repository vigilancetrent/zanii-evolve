# Copyright 2026 zaniiai agency.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The one way Evolve calls a service that holds a bearer token (Blackbox, a vendor).

- https only; plain http only to a loopback host; file:// and the rest refused
- redirects are never followed: urllib would resend the Authorization header to
  wherever a 30x points, so a redirect is an error naming the new host
- with ZANII_EVOLVE_OFFLINE=1 the host must be local (providers.local_url)
- at most `max_bytes` are read; a longer reply is an error
- errors name the host and status, never the headers (the token)
"""

from __future__ import annotations

import ipaddress
import urllib.error
import urllib.parse
import urllib.request

from . import providers as P

MAX_BYTES = 8_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlsplit(newurl).hostname
        raise RuntimeError(f"{urllib.parse.urlsplit(req.full_url).hostname} answered {code}, "
                           f"a redirect to {host}: not followed (it would carry the token)")


_OPENER = urllib.request.build_opener(_NoRedirect)


def _loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url: str) -> str:
    """Why `url` may not be called, or "" when it may."""
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    scheme = u.scheme.lower()
    if not host:
        return f"{url!r}: no host"
    if scheme != "https" and not (scheme == "http" and _loopback(host)):
        return f"{scheme or 'no'} scheme for {host}: use https (plain http only to loopback)"
    if P.offline() and not P.local_url(url):
        return f"ZANII_EVOLVE_OFFLINE is set: {host} is not a local host"
    return ""


def request(method: str, url: str, headers: dict | None = None, body: bytes | None = None,
            timeout: float = 30, max_bytes: int = MAX_BYTES, truncate: bool = False) -> bytes:
    """The reply body. Raises RuntimeError for a refused URL, a redirect or an
    oversized reply (with `truncate`, returns max_bytes + 1 bytes instead, for a
    caller that skips what is too long); urllib.error.HTTPError (a status >= 400)
    and OSError pass up."""
    why = check_url(url)
    if why:
        raise RuntimeError(why)
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    with _OPENER.open(req, timeout=timeout) as r:
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes and not truncate:
        raise RuntimeError(f"{urllib.parse.urlsplit(url).hostname} replied with more than "
                           f"{max_bytes} bytes")
    return data
