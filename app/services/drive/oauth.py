import os
import logging
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from dotenv import load_dotenv
from app.core.config import settings
from app.services.drive.token_store import persist_and_cache_tokens

_OAUTH_STATE_SEP = "###"


def _encode_oauth_state(tenant_id: str, user_id: str, code_verifier: str) -> str:
    # We include PKCE code verifier in state so callback can exchange the auth
    # code successfully on stateless multi-worker deployments.
    return f"{tenant_id}{_OAUTH_STATE_SEP}{user_id}{_OAUTH_STATE_SEP}{code_verifier}"


def _decode_oauth_state(state: str) -> tuple[str, str, str | None]:
    if _OAUTH_STATE_SEP in state:
        parts = state.split(_OAUTH_STATE_SEP)
        if len(parts) >= 3:
            tid, uid, code_verifier = parts[0], parts[1], _OAUTH_STATE_SEP.join(parts[2:])
            return (tid.strip() or settings.DEFAULT_TENANT_ID, uid, code_verifier)
        if len(parts) == 2:
            tid, uid = parts[0], parts[1]
            return (tid.strip() or settings.DEFAULT_TENANT_ID, uid, None)
        # Fallback: treat the entire state as user_id
        return settings.DEFAULT_TENANT_ID, state, None
    return settings.DEFAULT_TENANT_ID, state, None


def _generate_code_verifier() -> str:
    # RFC 7636: verifier is 43-128 chars. token_urlsafe(32) yields ~43-44 chars.
    v = secrets.token_urlsafe(32)
    return v[:128]

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

def create_flow(state: str | None = None, code_verifier: str | None = None) -> Flow:
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
        code_verifier=code_verifier,
        # If we pass code_verifier we want to use it as-is.
        autogenerate_code_verifier=code_verifier is None,
    )

@router.get("/drive/oauth/start")
def oauth_start(
    user_id: str,
    tenant_id: str | None = Query(
        None,
        description="Same value as X-Tenant-Id on API calls; defaults to server DEFAULT_TENANT_ID.",
    ),
):
    tid = (tenant_id or "").strip() or settings.DEFAULT_TENANT_ID
    code_verifier = _generate_code_verifier()
    state = _encode_oauth_state(tid, user_id, code_verifier)
    flow = create_flow(state=state, code_verifier=code_verifier)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(auth_url)

@router.get("/drive/oauth/callback")
def oauth_callback(code: str, state: str):
    tenant_id, user_id, code_verifier = _decode_oauth_state(state)

    try:
        if not code_verifier:
            raise ValueError("Missing PKCE code verifier in OAuth state")

        flow = create_flow(state=state, code_verifier=code_verifier)
        flow.fetch_token(code=code)
        creds = flow.credentials

        persist_and_cache_tokens(
            tenant_id,
            user_id,
            creds.token,
            creds.refresh_token,
        )
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