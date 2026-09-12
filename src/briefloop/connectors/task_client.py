"""Minimal stdlib client for a host-issued task capability; no authorization API."""
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--access-file', required=True)
    parser.add_argument('--request', required=True, help='JSON request file')
    args = parser.parse_args()
    access = json.loads(Path(args.access_file).read_text())
    body = Path(args.request).read_bytes()
    request = Request(access['url'], body, headers={'Authorization': 'Bearer ' + access['access_token'], 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=660) as response:
            print(response.read().decode('utf-8'))
    except HTTPError as exc:
        print(exc.read().decode('utf-8'))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
