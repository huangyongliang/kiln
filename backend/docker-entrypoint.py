#!/usr/bin/env python3
"""Prepare the artifact directory, then run the server as uid 10001.

The image entrypoint may start as root so a fresh named volume is writable by
the kiln user. The API process itself is not left running as root.
"""

import os
import sys

KILN_UID = 10001
KILN_GID = 10001


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("kiln-entrypoint requires a command")
    root = os.environ.get("KILN_ARTIFACT_ROOT", "/var/lib/kiln/artifacts")
    os.makedirs(root, exist_ok=True)
    os.chown(root, KILN_UID, KILN_GID)
    if os.geteuid() == 0:
        os.setgid(KILN_GID)
        os.setuid(KILN_UID)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
