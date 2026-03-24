import os
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from dotenv import load_dotenv
from app.core.config import settings
from app.services.drive.token_store import TOKEN_STORE

load_dotenv()

router = APIRouter()
logger = logging.getLogger(__name__)

# Must match what Google returns on token exchange. If the consent screen adds
# OpenID / profile scopes, oauthlib raises Warning("Scope has changed...") and
# fetch_token fails unless we request the same set here.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive.readonly",
]

def create_flow(state: str | None = None) -> Flow:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")

    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(status_code=500, detail="Missing GOOGLE_* env variables")

    return Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=SCOPES,
        state=state,
        redirect_uri=redirect_uri,
    )

@router.get("/drive/oauth/start")
def oauth_start(user_id: str):
    flow = create_flow(state=user_id)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(auth_url)

@router.get("/drive/oauth/callback")
def oauth_callback(code: str, state: str):
    user_id = state

    try:
        flow = create_flow(state=state)
        flow.fetch_token(code=code)
        creds = flow.credentials

        TOKEN_STORE[user_id] = {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
        }
    except Exception as e:
        logger.exception("oauth_callback_failed user_id=%s error=%s", user_id, type(e).__name__)
        if settings.FRONTEND_URL:
            qs = urlencode({
                "status": "error",
                "user_id": user_id,
                "message": f"oauth_callback_failed:{type(e).__name__}",
            })
            return RedirectResponse(f"{settings.FRONTEND_URL.rstrip('/')}/oauth-result?{qs}")
        raise HTTPException(status_code=500, detail=f"OAuth callback failed: {type(e).__name__}") from e

    if settings.FRONTEND_URL:
        qs = urlencode({
            "status": "success",
            "user_id": user_id,
            "refresh_token_present": str(bool(creds.refresh_token)).lower(),
        })
        return RedirectResponse(f"{settings.FRONTEND_URL.rstrip('/')}/oauth-result?{qs}")

    return {
        "connected_for_user": user_id,
        "refresh_token_present": bool(creds.refresh_token),
        "next": f"/drive/files?user_id={user_id}",
    }