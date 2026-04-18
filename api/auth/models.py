"""인증 관련 Pydantic 모델"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    nickname: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("비밀번호는 최소 8자 이상이어야 합니다")
        return v

    @field_validator("nickname")
    @classmethod
    def nickname_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("닉네임을 입력해주세요")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserInDB(BaseModel):
    """DB에서 조회한 사용자 정보"""
    id: int
    email: str
    nickname: str
    role: str  # 'admin' | 'moderator' | 'user'
    tier: str = "free"  # 'free' | 'pro' | 'premium'
    # NULL 의미:
    #   tier='free'  → 의미 없음 (항상 NULL)
    #   tier='pro'/'premium' → 만료 체크 불필요 (영구 부여 또는 수동 관리)
    tier_expires_at: Optional[datetime] = None
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None

    def effective_tier(self) -> str:
        """만료된 유료 티어는 free로 취급.

        tier_expires_at은 DB 컬럼이 TIMESTAMP(naive)이므로 UTC로 가정해 비교한다.
        향후 TIMESTAMPTZ로 이전해도 안전하도록 naive/aware 양쪽을 모두 처리.
        """
        if self.tier == "free" or self.tier_expires_at is None:
            return self.tier

        expires = self.tier_expires_at
        if expires.tzinfo is None:
            # DB에서 온 naive 값 — UTC로 간주
            now = datetime.utcnow()
        else:
            now = datetime.now(expires.tzinfo)

        if now > expires:
            return "free"
        return self.tier
