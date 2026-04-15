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
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None
