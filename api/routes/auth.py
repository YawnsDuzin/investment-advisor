"""인증 라우트 — 회원가입, 로그인, 로그아웃, 토큰 갱신"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from shared.config import AuthConfig, DatabaseConfig
from shared.db import get_connection
from psycopg2.extras import RealDictCursor
from api.auth.password import hash_password, verify_password
from api.auth.jwt_handler import (
    create_access_token, create_refresh_token, hash_token, decode_access_token,
)

router = APIRouter(prefix="/auth", tags=["인증"])

templates = Jinja2Templates(directory="api/templates")


def _get_auth_cfg() -> AuthConfig:
    return AuthConfig()


def _get_db_cfg() -> DatabaseConfig:
    return DatabaseConfig()


def _set_auth_cookies(response, access_token: str, refresh_token: str, auth_cfg: AuthConfig):
    """Access + Refresh Token 쿠키 설정"""
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=auth_cfg.cookie_secure,
        samesite="lax",
        max_age=auth_cfg.access_token_expire_minutes * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=auth_cfg.cookie_secure,
        samesite="lax",
        max_age=auth_cfg.refresh_token_expire_days * 86400,
        path="/auth/refresh",
    )


def _clear_auth_cookies(response):
    """인증 쿠키 삭제"""
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/auth/refresh")


# ── 페이지 ────────────────────────────────────


@router.get("/login")
def login_page(request: Request, error: str = "", next: str = "/"):
    return templates.TemplateResponse(request=request, name="login.html", context={
        "active_page": "login",
        "error": error,
        "next_url": next,
    })


@router.get("/register")
def register_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request=request, name="register.html", context={
        "active_page": "register",
        "error": error,
    })


# ── 회원가입 ──────────────────────────────────


@router.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    nickname: str = Form(...),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    # 비밀번호 최소 길이 검증
    if len(password) < 8:
        return templates.TemplateResponse(request=request, name="register.html", context={
            "active_page": "register",
            "error": "비밀번호는 최소 8자 이상이어야 합니다",
        })

    nickname = nickname.strip()
    if not nickname:
        return templates.TemplateResponse(request=request, name="register.html", context={
            "active_page": "register",
            "error": "닉네임을 입력해주세요",
        })

    pw_hash = hash_password(password)
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 이메일 중복 검사
            cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                return templates.TemplateResponse(request=request, name="register.html", context={
                    "active_page": "register",
                    "error": "이미 등록된 이메일입니다",
                })

            cur.execute(
                "INSERT INTO users (email, password_hash, nickname, role) "
                "VALUES (%s, %s, %s, 'user') RETURNING id, role",
                (email, pw_hash, nickname),
            )
            user = cur.fetchone()
        conn.commit()

        # 자동 로그인
        access_token = create_access_token(
            user["id"], user["role"],
            auth_cfg.jwt_secret_key, auth_cfg.jwt_algorithm,
            auth_cfg.access_token_expire_minutes,
        )
        refresh_raw = create_refresh_token()

        conn2 = get_connection(db_cfg)
        try:
            with conn2.cursor() as cur2:
                expires_at = datetime.now(timezone.utc) + timedelta(days=auth_cfg.refresh_token_expire_days)
                cur2.execute(
                    "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                    (user["id"], hash_token(refresh_raw), expires_at),
                )
            conn2.commit()
        finally:
            conn2.close()

        response = RedirectResponse("/", status_code=302)
        _set_auth_cookies(response, access_token, refresh_raw, auth_cfg)
        return response

    finally:
        conn.close()


# ── 로그인 ────────────────────────────────────


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, email, password_hash, role, is_active FROM users WHERE email = %s",
                (email,),
            )
            user = cur.fetchone()

            if not user or not user["password_hash"]:
                return templates.TemplateResponse(request=request, name="login.html", context={
                    "active_page": "login",
                    "error": "이메일 또는 비밀번호가 올바르지 않습니다",
                    "next_url": next,
                })

            if not verify_password(password, user["password_hash"]):
                return templates.TemplateResponse(request=request, name="login.html", context={
                    "active_page": "login",
                    "error": "이메일 또는 비밀번호가 올바르지 않습니다",
                    "next_url": next,
                })

            if not user["is_active"]:
                return templates.TemplateResponse(request=request, name="login.html", context={
                    "active_page": "login",
                    "error": "비활성화된 계정입니다. 관리자에게 문의하세요.",
                    "next_url": next,
                })

            # last_login_at 업데이트 + 기존 refresh token 폐기
            cur.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user["id"],))
            cur.execute(
                "UPDATE refresh_tokens SET revoked_at = NOW() "
                "WHERE user_id = %s AND revoked_at IS NULL AND expires_at > NOW()",
                (user["id"],),
            )

            # 새 토큰 발급
            access_token = create_access_token(
                user["id"], user["role"],
                auth_cfg.jwt_secret_key, auth_cfg.jwt_algorithm,
                auth_cfg.access_token_expire_minutes,
            )
            refresh_raw = create_refresh_token()
            expires_at = datetime.now(timezone.utc) + timedelta(days=auth_cfg.refresh_token_expire_days)
            cur.execute(
                "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                (user["id"], hash_token(refresh_raw), expires_at),
            )

        conn.commit()

        response = RedirectResponse(next or "/", status_code=302)
        _set_auth_cookies(response, access_token, refresh_raw, auth_cfg)
        return response

    finally:
        conn.close()


# ── 로그아웃 ──────────────────────────────────


@router.post("/logout")
def logout(
    request: Request,
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    # Refresh token DB 폐기
    refresh_cookie = request.cookies.get("refresh_token")
    if refresh_cookie:
        conn = get_connection(db_cfg)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE refresh_tokens SET revoked_at = NOW() WHERE token_hash = %s AND revoked_at IS NULL",
                    (hash_token(refresh_cookie),),
                )
            conn.commit()
        finally:
            conn.close()

    response = RedirectResponse("/", status_code=302)
    _clear_auth_cookies(response)
    return response


# ── 토큰 갱신 (Refresh Token Rotation) ────────


@router.post("/refresh")
def refresh_token(
    request: Request,
    auth_cfg: AuthConfig = Depends(_get_auth_cfg),
    db_cfg: DatabaseConfig = Depends(_get_db_cfg),
):
    refresh_cookie = request.cookies.get("refresh_token")
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail="Refresh token이 없습니다")

    token_hash = hash_token(refresh_cookie)
    conn = get_connection(db_cfg)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT rt.id, rt.user_id, rt.revoked_at, u.role, u.is_active "
                "FROM refresh_tokens rt JOIN users u ON rt.user_id = u.id "
                "WHERE rt.token_hash = %s AND rt.expires_at > NOW()",
                (token_hash,),
            )
            rt = cur.fetchone()

            if not rt:
                raise HTTPException(status_code=401, detail="유효하지 않은 Refresh token")

            # 탈취 감지: 이미 폐기된 토큰 재사용 → 해당 user의 모든 토큰 일괄 폐기
            if rt["revoked_at"] is not None:
                cur.execute(
                    "UPDATE refresh_tokens SET revoked_at = NOW() "
                    "WHERE user_id = %s AND revoked_at IS NULL",
                    (rt["user_id"],),
                )
                conn.commit()
                response = RedirectResponse("/auth/login", status_code=302)
                _clear_auth_cookies(response)
                return response

            if not rt["is_active"]:
                raise HTTPException(status_code=401, detail="비활성화된 계정입니다")

            # 기존 토큰 폐기 + 새 토큰 발급
            cur.execute("UPDATE refresh_tokens SET revoked_at = NOW() WHERE id = %s", (rt["id"],))

            access_token = create_access_token(
                rt["user_id"], rt["role"],
                auth_cfg.jwt_secret_key, auth_cfg.jwt_algorithm,
                auth_cfg.access_token_expire_minutes,
            )
            new_refresh_raw = create_refresh_token()
            expires_at = datetime.now(timezone.utc) + timedelta(days=auth_cfg.refresh_token_expire_days)
            cur.execute(
                "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
                (rt["user_id"], hash_token(new_refresh_raw), expires_at),
            )

        conn.commit()

        response = RedirectResponse("/", status_code=302)
        _set_auth_cookies(response, access_token, new_refresh_raw, auth_cfg)
        return response

    finally:
        conn.close()
