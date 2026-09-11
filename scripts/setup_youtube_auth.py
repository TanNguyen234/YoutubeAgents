"""Interactive CLI tool to authenticate YouTube Channel OAuth 2.0 credentials."""

import http.server
import json
import os
from pathlib import Path
import socketserver
import sys
import urllib.parse
import webbrowser

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.youtube_oauth import YouTubeOAuthManager, YouTubeOAuthError


def main():
    print("=" * 70)
    print("       YOUTUBE CHANNEL OAUTH 2.0 AUTHENTICATION SETUP")
    print("=" * 70)

    manager = YouTubeOAuthManager()

    if not manager.has_client_secrets():
        print("\n[!] 'client_secrets.json' was NOT found in data/credentials/ or root.")
        print("\nTo enable REAL live uploads to your YouTube channel:")
        print("1. Open Google Cloud Console: https://console.cloud.google.com/")
        print("2. Enable 'YouTube Data API v3'.")
        print("3. Go to 'APIs & Services' > 'Credentials' > 'Create Credentials' > 'OAuth Client ID'.")
        print("   - Application Type: Desktop app (or Web Application with redirect: http://localhost:8080/)")
        print("4. Download the JSON file and save it as:")
        print("   -> d:\\Download\\YoutubeAgents\\data\\credentials\\client_secrets.json")
        print("\nAfter placing the file, re-run this script: python scripts/setup_youtube_auth.py")
        sys.exit(1)

    print("\n[+] Found client_secrets.json!")

    if manager.has_valid_token():
        print("[+] Existing token.json found. Testing refresh / validity...")
        try:
            token = manager.get_access_token()
            print(f"[SUCCESS] Token is valid and ready for live YouTube uploads! (prefix: {token[:12]}...)")
            print("You are ready to upload videos with: python scripts/run_real_full_pipeline.py")
            return
        except Exception as e:
            print(f"[!] Existing token expired or invalid ({e}). Starting fresh login...")

    redirect_uri = "http://localhost:8080/"
    auth_url = manager.generate_auth_url(redirect_uri=redirect_uri)

    print("\n" + "-" * 70)
    print("Opening browser for Google Account consent...")
    print("URL: " + auth_url)
    print("-" * 70)

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("\nWaiting for authorization response on http://localhost:8080/ ...")

    auth_code = None

    class OAuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if "code" in query:
                auth_code = query["code"][0]
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<h1>Authentication Successful!</h1><p>You can close this tab and return to the terminal.</p>")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h1>Authentication Failed!</h1>")

        def log_message(self, format, *args):
            pass  # Silence standard HTTP logs

    server_address = ("", 8080)
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(server_address, OAuthCallbackHandler) as httpd:
            httpd.handle_request()
    except Exception as e:
        print(f"\n[!] Could not start local callback server on port 8080: {e}")
        auth_code = input("\nPlease paste the 'code' parameter from the redirected browser URL here: ").strip()

    if not auth_code:
        print("[!] No authorization code received. Setup aborted.")
        sys.exit(1)

    print(f"\n[+] Authorization code captured. Exchanging for tokens...")
    try:
        token_data = manager.exchange_code_for_token(auth_code=auth_code, redirect_uri=redirect_uri)
        print("\n" + "=" * 70)
        print("[SUCCESS] YouTube authentication successfully established and saved to:")
        print(f"          {manager.token_path}")
        print("You can now run full live production uploads with real publication!")
        print("=" * 70)
    except Exception as e:
        print(f"[ERROR] Failed to exchange code for token: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
