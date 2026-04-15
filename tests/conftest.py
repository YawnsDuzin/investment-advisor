"""테스트 설정 — 외부 의존성(psycopg2, feedparser 등) mock 처리

실제 DB나 Claude SDK 토큰을 사용하지 않고 단위 테스트를 실행합니다.
"""
import sys
from unittest.mock import MagicMock

# psycopg2 mock — DB 연결 없이 테스트
_psycopg2_mock = MagicMock()
_psycopg2_mock.extras = MagicMock()
_psycopg2_mock.extras.RealDictCursor = MagicMock()
_psycopg2_mock.extras.execute_values = MagicMock()
sys.modules.setdefault("psycopg2", _psycopg2_mock)
sys.modules.setdefault("psycopg2.extras", _psycopg2_mock.extras)

# feedparser mock — RSS 없이 테스트
sys.modules.setdefault("feedparser", MagicMock())

# claude_agent_sdk mock — SDK 토큰 없이 테스트
_sdk_mock = MagicMock()

class _MockClaudeAgentOptions:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

_sdk_mock.ClaudeAgentOptions = _MockClaudeAgentOptions
_sdk_mock.AssistantMessage = type("AssistantMessage", (), {})
_sdk_mock.TextBlock = type("TextBlock", (), {})
sys.modules.setdefault("claude_agent_sdk", _sdk_mock)
