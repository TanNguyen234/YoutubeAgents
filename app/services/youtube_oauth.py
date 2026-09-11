"""YouTube OAuth 2.0 Authentication Manager for YouTube Data API v3."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Dict, Optional
import urllib.parse
import httpx


DEFAULT_SECRETS_PATHS = [
    Path("data/credentials/client_secrets.json"),
    Path("credentials/client_secrets.json"),
    Path("client_secrets.json"),
]

DEFAULT_TOKEN_PATHS = [
    Path("data/credentials/token.json"),
    Path("credentials/token.json"),
    Path("token.json"),
]

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class YouTubeOAuthError(RuntimeError):
    """Raised when YouTube OAuth operations fail."""
    pass


class YouTubeOAuthManager:
    """Manages YouTube OAuth 2.0 credentials, token refresh, and authorization flows."""

    def __init__(
        self,
        secrets_path: Optional[Path] = None,
        token_path: Optional[Path] = None,
    ):
        self.secrets_path = secrets_path or self._discover_file(DEFAULT_SECRETS_PATHS, "YOUTUBE_CLIENT_SECRETS_FILE")
        self.token_path = token_path or self._discover_file(DEFAULT_TOKEN_PATHS, "YOUTUBE_TOKEN_FILE", default_fallback=Path("data/credentials/token.json"))

    @staticmethod
    def _discover_file(candidates: list[Path], env_var: str, default_fallback: Optional[Path] = None) -> Optional[Path]:
        env_val = os.environ.get(env_var)
        if env_val and Path(env_val).exists():
            return Path(env_val)
        for p in candidates:
            if p.exists():
                return p
        return default_fallback if default_fallback else None

    def has_client_secrets(self) -> bool:
        """Check if client_secrets.json is present."""
        return self.secrets_path is not None and self.secrets_path.exists()

    def has_valid_token(self) -> bool:
        """Check if token.json is present and has access/refresh credentials."""
        if not self.token_path or not self.token_path.exists():
            return False
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
            return bool(data.get("access_token") or data.get("refresh_token"))
        except Exception:
            return False

    def load_client_secrets(self) -> Dict[str, str]:
        """Parse client_id and client_secret from client_secrets.json."""
        if not self.has_client_secrets():
            raise YouTubeOAuthError(
                "Missing 'client_secrets.json'. Please download OAuth 2.0 Client Credentials from "
                "Google Cloud Console (YouTube Data API v3 enabled) and save to 'data/credentials/client_secrets.json'."
            )
        data = json.loads(self.secrets_path.read_text(encoding="utf-8"))
        cfg = data.get("installed") or data.get("web") or data
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        if not client_id or not client_secret:
            raise YouTubeOAuthError("Malformed client_secrets.json: missing 'client_id' or 'client_secret'.")
        return {"client_id": client_id, "client_secret": client_secret}

    def generate_auth_url(self, redirect_uri: str = "http://localhost:8080/") -> str:
        """Generate the Google OAuth 2.0 authorization URL for human consent."""
        secrets = self.load_client_secrets()
        params = {
            "client_id": secrets["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": f"{YOUTUBE_UPLOAD_SCOPE} {YOUTUBE_READONLY_SCOPE}",
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code_for_token(self, auth_code: str, redirect_uri: str = "http://localhost:8080/") -> Dict[str, str]:
        """Exchange authorization code for access and refresh tokens and persist to token.json."""
        secrets = self.load_client_secrets()
        data = {
            "code": auth_code,
            "client_id": secrets["client_id"],
            "client_secret": secrets["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(GOOGLE_TOKEN_URL, data=data)
            if resp.status_code != 200:
                raise YouTubeOAuthError(f"OAuth token exchange failed ({resp.status_code}): {resp.text}")
            token_data = resp.json()
        expires_in = token_data.get("expires_in", 3600)
        token_data["expires_at"] = datetime.now(timezone.utc).timestamp() + expires_in

        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
        return token_data

    def get_access_token(self) -> str:
        """Get valid access token, automatically refreshing via refresh_token if necessary."""
        if not self.has_valid_token():
            raise YouTubeOAuthError(
                "No valid YouTube token found. Please run 'python scripts/setup_youtube_auth.py' to authenticate."
            )

        token_data = json.loads(self.token_path.read_text(encoding="utf-8"))
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        now_ts = datetime.now(timezone.utc).timestamp()
        expires_at = float(token_data.get("expires_at", 0))

        # Return cached access token if still valid (with 5-minute safety margin)
        if access_token and expires_at > (now_ts + 300):
            return access_token

        # Refresh if refresh_token is available
        if refresh_token and self.has_client_secrets():
            secrets = self.load_client_secrets()
            refresh_payload = {
                "client_id": secrets["client_id"],
                "client_secret": secrets["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(GOOGLE_TOKEN_URL, data=refresh_payload)
                    if resp.status_code == 200:
                        new_tokens = resp.json()
                        expires_in = new_tokens.get("expires_in", 3600)
                        new_tokens["expires_at"] = now_ts + expires_in
                        token_data.update(new_tokens)
                        self.token_path.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
                        return token_data["access_token"]
            except Exception as e:
                import logging
                logging.getLogger("youtube_oauth").warning(f"Failed to refresh YouTube OAuth token: {e}")

        return access_token
