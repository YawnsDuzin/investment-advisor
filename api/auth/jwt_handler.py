"""JWT 토큰 발급/검증 + Refresh Token 해싱"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError


def create_access_token(user_id: int, role: str, secret_key: str,
                        algorithm: str = "HS256",
                        expire_minutes: int = 60) -> str:
    """Access Token 발급"""
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, secret_key, algorithm=algorithm)


def decode_access_token(token: str, secret_key: str,
                        algorithm: str = "HS256") -> dict | None:
    """Access Token 디코딩. 유효하지 않으면 None 반환"""
    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


def create_refresh_token() -> str:
    """랜덤 Refresh Token 문자열 생성 (DB에는 해시로 저장)"""
    return secrets.token_urlsafe(48)


def hash_token(raw: str) -> str:
    """Refresh Token을 SHA-256 해시로 변환 (DB 저장용)"""
    return hashlib.sha256(raw.encode()).hexdigest()
