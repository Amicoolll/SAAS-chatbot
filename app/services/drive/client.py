import os

import httplib2
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

from app.core.config import settings

load_dotenv()

# Keep in sync with app.services.drive.oauth.SCOPES (token was issued for this set).
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive.readonly",
]

def build_drive_service(access_token: str, refresh_token: str):
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES,
    )
    base_http = httplib2.Http(timeout=settings.DRIVE_HTTP_TIMEOUT_SEC)
    authed_http = AuthorizedHttp(creds, http=base_http)
    return build("drive", "v3", http=authed_http, cache_discovery=False)