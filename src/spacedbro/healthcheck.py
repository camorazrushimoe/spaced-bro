"""Container HEALTHCHECK probe: GET /healthz and exit 0 on 200."""

from __future__ import annotations

import sys
import urllib.request


def main() -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/healthz", timeout=3) as resp:
            if resp.status == 200:
                sys.exit(0)
    except Exception:
        pass
    sys.exit(1)


if __name__ == "__main__":
    main()
