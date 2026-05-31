from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from baserender_api.auth.session import (
    create_session_token,
    get_auth_config,
    is_valid_session_token,
    password_matches,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class SessionResponse(BaseModel):
    authenticated: bool


@router.post("/login", response_model=SessionResponse)
def login(request: LoginRequest, response: Response) -> SessionResponse:
    config = _load_auth_config()

    if not password_matches(request.password, config):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password.")

    response.set_cookie(
        config.cookie_name,
        create_session_token(config),
        httponly=True,
        secure=config.secure_cookie,
        samesite="lax",
        max_age=config.session_ttl_seconds,
        path="/",
    )
    return SessionResponse(authenticated=True)


@router.post("/logout", response_model=SessionResponse)
def logout(response: Response) -> SessionResponse:
    config = _load_auth_config()
    response.delete_cookie(config.cookie_name, path="/", samesite="lax")
    return SessionResponse(authenticated=False)


@router.get("/session", response_model=SessionResponse)
def session(request: Request) -> SessionResponse:
    config = _load_auth_config()
    token = request.cookies.get(config.cookie_name)
    return SessionResponse(authenticated=is_valid_session_token(token, config))


def _load_auth_config():
    try:
        return get_auth_config()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
