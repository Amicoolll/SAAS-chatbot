import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from dotenv import load_dotenv
from app.services.drive.token_store import TOKEN_STORE

load_dotenv()

router = APIRouter()

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

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

    flow = create_flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials

    TOKEN_STORE[user_id] = {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
    }

    return {
        "connected_for_user": user_id,
        "refresh_token_present": bool(creds.refresh_token),
        "next": f"/drive/files?user_id={user_id}",
    }