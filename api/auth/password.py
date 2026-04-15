"""비밀번호 해싱 (bcrypt)"""
from passlib.context import CryptContext

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """평문 비밀번호를 bcrypt 해시로 변환"""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """평문 비밀번호와 bcrypt 해시 비교"""
    return _pwd_ctx.verify(plain, hashed)
