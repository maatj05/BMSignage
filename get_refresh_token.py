#!/usr/bin/env python3
"""One-time helper: links this Pi to Dropbox and prints a refresh token.

Uses the PKCE flow, so the app secret never has to be stored on the Pi.
Usage: python3 get_refresh_token.py <app_key>
"""

import base64
import hashlib
import secrets
import sys
import urllib.parse

import requests


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Gebruik: python3 get_refresh_token.py <app_key>")
    app_key = sys.argv[1]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = "https://www.dropbox.com/oauth2/authorize?" + urllib.parse.urlencode({
        "client_id": app_key,
        "response_type": "code",
        "token_access_type": "offline",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    print("1. Open deze link in een browser en klik op 'Toestaan':\n")
    print("   " + url + "\n")
    code = input("2. Plak hier de code die Dropbox toont: ").strip()
    r = requests.post("https://api.dropboxapi.com/oauth2/token", data={
        "code": code,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
        "client_id": app_key,
    }, timeout=30)
    if not r.ok:
        sys.exit(f"Mislukt: {r.status_code} {r.text}")
    print("\n3. Zet dit in settings.toml onder [dropbox]:\n")
    print(f'   app_key = "{app_key}"')
    print(f'   refresh_token = "{r.json()["refresh_token"]}"')


if __name__ == "__main__":
    main()
