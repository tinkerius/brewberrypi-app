"""Write the Cloud Sync Google client into the package (Containerfile, docs/deployment/cloud-sync-google.md).

Usage: google-drive-client.py <secrets dir> <output file>

Reads the build secrets `google_drive_client_id` and `google_drive_client_secret` from the secrets directory
and writes `{"clientId", "clientSecret"}` to the output file. Without them nothing is written, and the image
has no built-in client (Cloud Sync unavailable, SYNC-001). Never prints the values.
"""

import json
import sys
from pathlib import Path


def read(directory: Path, name: str) -> str:
    try:
        return (directory / name).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def main() -> int:
    directory, output = Path(sys.argv[1]), Path(sys.argv[2])
    client_id = read(directory, "google_drive_client_id")
    client_secret = read(directory, "google_drive_client_secret")
    if not client_id and not client_secret:
        print("no Cloud Sync Google client given: Cloud Sync is unavailable in this image")
        return 0
    if not client_id or not client_secret:
        print("only one of google_drive_client_id and google_drive_client_secret given", file=sys.stderr)
        return 1
    output.write_text(
        json.dumps({"clientId": client_id, "clientSecret": client_secret}) + "\n", encoding="utf-8"
    )
    print("Cloud Sync Google client built in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
