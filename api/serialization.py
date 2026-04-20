"""DB row 직렬화 헬퍼 — sessions.py에서 추출 (B1).

RealDictRow의 date/datetime/Decimal 타입을 JSON 직렬화 가능 형태로 변환한다.
sessions/chat/education/inquiry/pages 라우트에서 공통으로 사용.
"""
from datetime import date, datetime
from decimal import Decimal


def serialize_row(row: dict) -> dict:
    """RealDictRow의 date/datetime/Decimal 타입을 JSON 직렬화 가능하도록 변환."""
    result = {}
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            result[k] = v.isoformat()
        elif isinstance(v, Decimal):
            result[k] = float(v)
        else:
            result[k] = v
    return result
