import logging
import os

import httplib2
from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.core.config import settings

load_dotenv()

logger = logging.getLogger(__name__)

# Keep in sync with app.services.drive.oauth.SCOPES (token was issued for this set).
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive.readonly",
]


class DriveReconnectRequired(Exception):
    """Raised when Drive tokens can no longer be refreshed (revoked, expired, or
    the user never granted offline access). The user must re-run the OAuth flow.
    Distinct from transient 5xx / network errors — surfacing this lets callers
    show a clear "Reconnect Google Drive" CTA rather than a generic failure.
    """


def build_drive_credentials(access_token: str, refresh_token: str | None) -> Credentials:
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES,
    )


def build_drive_service_from_credentials(creds: Credentials):
    base_http = httplib2.Http(timeout=settings.DRIVE_HTTP_TIMEOUT_SEC)
    authed_http = AuthorizedHttp(creds, http=base_http)
    return build("drive", "v3", http=authed_http, cache_discovery=False)


def build_drive_service(access_token: str, refresh_token: str):
    return build_drive_service_from_credentials(
        build_drive_credentials(access_token, refresh_token)
    )


def refresh_and_persist_tokens(tenant_id: str, user_id: str) -> Credentials:
    """Force a token refresh and persist the new access token so every worker
    sees the latest value. Raises :class:`DriveReconnectRequired` when the
    refresh token is missing, revoked, or otherwise rejected by Google.

    Imports token_store lazily to avoid a circular import at module load.
    """
    from app.services.drive.token_store import TOKEN_STORE, persist_and_cache_tokens

    tokens = TOKEN_STORE.get(user_id) or {}
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise DriveReconnectRequired(
            "No refresh token on file — user must reconnect Google Drive."
        )

    creds = build_drive_credentials(tokens.get("access_token") or "", refresh_token)
    try:
        creds.refresh(Request())
    except RefreshError as e:
        # Refresh token revoked / expired / scope mismatch — cannot recover
        # without user re-consent.
        TOKEN_STORE.pop(user_id, None)
        logger.warning(
            "drive_token_refresh_failed tenant=%s user=%s error=%s",
            tenant_id,
            user_id,
            e,
        )
        raise DriveReconnectRequired(
            "Google rejected the refresh token — please reconnect Google Drive."
        ) from e

    # Google may or may not rotate the refresh token; keep the existing one if
    # the response didn't include a new one.
    persist_and_cache_tokens(
        tenant_id,
        user_id,
        creds.token,
        creds.refresh_token or refresh_token,
    )
    return creds