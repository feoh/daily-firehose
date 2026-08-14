"""Container health probe for the web service.

Gunicorn is reached directly on loopback, so the probe supplies a configured
allowed Host header instead of the literal loopback address that production
``ALLOWED_HOSTS`` validation deliberately refuses.
"""

from __future__ import annotations

import http.client
import os
import sys


def _host_header() -> str:
    hosts = [
        host.strip()
        for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    return hosts[0] if hosts else "localhost"


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/health/ready"
    port = int(os.environ.get("WEB_CONTAINER_PORT", "8000"))
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers={"Host": _host_header()})
        response = connection.getresponse()
        body = response.read(2048).decode("utf-8", "replace")
    except OSError as exc:
        print(f"health probe failed: {exc}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    if response.status != 200:
        print(f"health probe status {response.status}: {body}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
