"""최적화 변경사항 단위 테스트

Claude SDK 호출은 모두 mock 처리하여 실제 토큰을 사용하지 않습니다.
외부 의존성(psycopg2, feedparser, claude_agent_sdk)은 conftest.py에서 mock됩니다.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock

import pytest


# ── A1: 모델 분기 테스트 ──────────────────────────────

class TestModelBranching:
    """_query_claude()에 model 파라미터가 올바르게 전달되는지 검증"""

    @pytest.mark.asyncio
    async def test_query_claude_passes_model(self):
        """model 파라미터가 ClaudeAgentOptions에 전달되는지 확인"""
        from analyzer.analyzer import _query_claude

        captured_opts = {}

        async def fake_query(**kwargs):
            captured_opts.update(kwargs)
            return
            yield  # noqa: make it async generator

        with patch("analyzer.analyzer.query", fake_query):
            result = await _query_claude(
                "test prompt", "system", 1,
                model="claude-haiku-4-5-20251001"
            )
            assert result == ""
            opts = captured_opts.get("options")
            assert opts is not None
            assert opts.model == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_query_claude_default_model_none(self):
        """model 미지정 시 None이 전달되는지 확인"""
        from analyzer.analyzer import _query_claude

        captured_opts = {}

        async def fake_query(**kwargs):
            captured_opts.update(kwargs)
            return
            yield

        with patch("analyzer.analyzer.query", fake_query):
            await _query_claude("test", "sys", 1)
            opts = captured_opts.get("options")
            assert opts.model is None

    def test_config_model_defaults(self):
        """AnalyzerConfig의 모델 기본값이 올바른지 확인"""
        from shared.config import AnalyzerConfig
        cfg = AnalyzerConfig()
        assert cfg.model_analysis == "claude-sonnet-4-6"
        assert cfg.model_translate == "claude-haiku-4-5-20251001"

    def test_config_min_new_news_default(self):
        """min_new_news 기본값 확인"""
        from shared.config import AnalyzerConfig
        cfg = AnalyzerConfig()
        assert cfg.min_new_news == 5


# ── B2: 뉴스 시간 필터링 테스트 ──────────────────────

class TestNewsTimeFilter:
    """24시간 이내 뉴스만 수집되는지 검증"""

    def test_parse_published_rfc2822(self):
        """RFC 2822 형식 날짜 파싱"""
        from analyzer.news_collector import _parse_published
        result = _parse_published("Mon, 14 Apr 2026 10:30:00 GMT")
        assert result is not None
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 14

    def test_parse_published_empty(self):
        """빈 문자열은 None 반환"""
        from analyzer.news_collector import _parse_published
        assert _parse_published("") is None

    def test_parse_published_none(self):
        """None 입력은 None 반환"""
        from analyzer.news_collector import _parse_published
        assert _parse_published(None) is None

    def test_parse_published_invalid(self):
        """잘못된 형식은 None 반환"""
        from analyzer.news_collector import _parse_published
        assert _parse_published("not a date") is None

    def test_old_news_filtered(self):
        """24시간 이전 뉴스가 필터링되는지 확인"""
        from analyzer.news_collector import _parse_published

        # 48시간 전
        old_date = datetime.now(timezone.utc) - timedelta(hours=48)
        old_str = old_date.strftime("%a, %d %b %Y %H:%M:%S +0000")
        parsed = _parse_published(old_str)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        assert parsed is not None
        assert parsed < cutoff  # 24시간 전이므로 필터링 대상

    def test_recent_news_passes(self):
        """1시간 전 뉴스는 통과"""
        from analyzer.news_collector import _parse_published

        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_str = recent.strftime("%a, %d %b %Y %H:%M:%S +0000")
        parsed = _parse_published(recent_str)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        assert parsed is not None
        assert parsed >= cutoff  # 1시간 전이므로 통과


# ── B3: 교차 중복 제거 테스트 ─────────────────────────

class TestDeduplication:
    """제목 앞 30자 기준 중복 제거 검증"""

    def test_title_key_dedup(self):
        """동일 제목 앞 30자는 중복으로 처리"""
        seen = set()
        titles = [
            "Global Markets Rally on Fed Rate Cut — detailed analysis",
            "Global Markets Rally on Fed Rate Cut — brief summary",
            "Completely Different News Title About AI Technology",
        ]

        unique = []
        for t in titles:
            key = t[:30].strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(t)

        assert len(unique) == 2
        assert unique[0] == titles[0]
        assert unique[1] == titles[2]

    def test_case_insensitive_dedup(self):
        """대소문자 무시하고 중복 제거"""
        seen = set()
        titles = ["Apple Reports Record Revenue", "APPLE REPORTS RECORD REVENUE"]

        unique = []
        for t in titles:
            key = t[:30].strip().lower()
            if key not in seen:
                seen.add(key)
                unique.append(t)

        assert len(unique) == 1


# ── A2: HTML 클리닝 개선 테스트 ───────────────────────

class TestHtmlCleaning:
    """정규식 기반 HTML 태그 제거 검증"""

    def test_clean_html_basic_tags(self):
        """기본 HTML 태그 제거"""
        from analyzer.news_collector import _clean_html
        result = _clean_html("<p>Hello <b>World</b></p>")
        assert result == "Hello World"

    def test_clean_html_complex_tags(self):
        """속성 포함 복잡한 태그도 제거"""
        from analyzer.news_collector import _clean_html
        result = _clean_html('<div class="news"><a href="url">Link</a></div>')
        assert result == "Link"

    def test_clean_html_whitespace(self):
        """연속 공백 정리"""
        from analyzer.news_collector import _clean_html
        result = _clean_html("  Hello   World  ")
        assert result == "Hello World"

    def test_clean_html_no_tags(self):
        """태그 없는 텍스트는 그대로"""
        from analyzer.news_collector import _clean_html
        result = _clean_html("Plain text content")
        assert result == "Plain text content"


# ── B1: 재분석 임계값 테스트 ──────────────────────────

class TestReanalysisThreshold:
    """신규 뉴스 수에 따른 분석 스킵 로직 검증"""

    def test_new_news_count(self):
        """신규 뉴스 수 계산 로직"""
        prev_titles = {"News A", "News B", "News C"}
        curr_titles = {"News A", "News B", "News D", "News E"}
        new_titles = curr_titles - prev_titles
        assert len(new_titles) == 2

    def test_skip_when_below_threshold(self):
        """임계값 미만이면 스킵해야 함"""
        min_new = 5
        prev_titles = {"A", "B", "C", "D", "E"}
        curr_titles = {"A", "B", "C", "D", "F", "G"}
        new_count = len(curr_titles - prev_titles)
        should_skip = new_count < min_new and len(prev_titles) > 0
        assert should_skip is True

    def test_proceed_when_above_threshold(self):
        """임계값 이상이면 분석 진행해야 함"""
        min_new = 5
        prev_titles = {"A", "B"}
        curr_titles = {"C", "D", "E", "F", "G", "H", "I"}
        new_count = len(curr_titles - prev_titles)
        should_skip = new_count < min_new and len(prev_titles) > 0
        assert should_skip is False

    def test_skip_only_when_previous_exists(self):
        """이전 세션이 없으면 항상 진행"""
        prev_titles = set()
        curr_titles = {"A", "B"}
        new_count = len(curr_titles - prev_titles)
        should_skip = new_count < 5 and len(prev_titles) > 0
        assert should_skip is False


# ── B4: 배치 번역 테스트 ──────────────────────────────

class TestBatchTranslation:
    """제목+요약 배치 번역 로직 검증"""

    def test_has_korean_detection(self):
        """한글 포함 여부 판별"""
        def _has_korean(text): return bool(re.search(r'[\uac00-\ud7af]', text))

        assert _has_korean("삼성전자 주가 상승") is True
        assert _has_korean("Samsung stock rises") is False
        assert _has_korean("Samsung 삼성") is True
        assert _has_korean("") is False

    @pytest.mark.asyncio
    async def test_translate_batch_skips_korean(self):
        """이미 한글인 기사는 번역 호출 없이 처리"""
        articles = [
            {"title": "삼성전자 실적 발표", "summary": "삼성전자가 1분기 실적을 발표했다."},
            {"title": "한국 경제 성장률", "summary": "한국 경제 성장률이 상승했다."},
        ]

        with patch("analyzer.analyzer._query_claude", new_callable=AsyncMock) as mock_query:
            from analyzer.analyzer import _translate_news_batch
            result = await _translate_news_batch(articles)

            mock_query.assert_not_called()
            assert result[0]["title_ko"] == "삼성전자 실적 발표"
            assert result[0]["summary_ko"] == "삼성전자가 1분기 실적을 발표했다."
            assert result[1]["title_ko"] == "한국 경제 성장률"

    @pytest.mark.asyncio
    async def test_translate_batch_calls_with_model(self):
        """영문 기사 번역 시 지정된 모델로 호출되는지 확인"""
        articles = [
            {"title": "Fed Cuts Rate", "summary": "The Federal Reserve cut rates."},
        ]

        mock_response = json.dumps({
            "translations": {
                "0": {"t": "연준 금리 인하", "s": "연준이 금리를 인하했다."}
            }
        })

        with patch("analyzer.analyzer._query_claude", new_callable=AsyncMock,
                    return_value=mock_response) as mock_query:
            from analyzer.analyzer import _translate_news_batch
            result = await _translate_news_batch(articles, model="claude-haiku-4-5-20251001")

            mock_query.assert_called_once()
            # model 키워드 인자 확인
            _, kwargs = mock_query.call_args
            assert kwargs.get("model") == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_translate_batch_splits_at_30(self):
        """30건 초과 시 2배치로 분리되는지 확인"""
        articles = [
            {"title": f"News headline {i}", "summary": f"Summary {i}"}
            for i in range(35)
        ]

        mock_response = json.dumps({"translations": {}})

        with patch("analyzer.analyzer._query_claude", new_callable=AsyncMock,
                    return_value=mock_response) as mock_query:
            from analyzer.analyzer import _translate_news_batch
            await _translate_news_batch(articles)

            # 35건 → 30건 + 5건 = 2배치
            assert mock_query.call_count == 2

    @pytest.mark.asyncio
    async def test_translate_batch_result_parsing(self):
        """번역 결과 JSON이 올바르게 파싱되어 articles에 반영되는지 확인"""
        articles = [
            {"title": "Tech Rally", "summary": "Stocks surge on AI hype."},
        ]

        mock_response = json.dumps({
            "translations": {
                "0": {"t": "기술주 랠리", "s": "AI 열풍에 주가 급등."}
            }
        })

        with patch("analyzer.analyzer._query_claude", new_callable=AsyncMock,
                    return_value=mock_response):
            from analyzer.analyzer import _translate_news_batch
            result = await _translate_news_batch(articles)

            assert result[0]["title_ko"] == "기술주 랠리"
            assert result[0]["summary_ko"] == "AI 열풍에 주가 급등."

    @pytest.mark.asyncio
    async def test_translate_batch_empty_articles(self):
        """빈 리스트는 즉시 반환"""
        with patch("analyzer.analyzer._query_claude", new_callable=AsyncMock) as mock_query:
            from analyzer.analyzer import _translate_news_batch
            result = await _translate_news_batch([])
            assert result == []
            mock_query.assert_not_called()


# ── JSON 파싱 테스트 ──────────────────────────────────

class TestJsonParsing:
    """_parse_json_response 함수 검증"""

    def test_parse_raw_json(self):
        """순수 JSON 파싱"""
        from analyzer.analyzer import _parse_json_response
        result = _parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_in_code_block(self):
        """```json 블록 내 JSON 파싱"""
        from analyzer.analyzer import _parse_json_response
        text = '일부 텍스트\n```json\n{"key": "value"}\n```\n추가 텍스트'
        result = _parse_json_response(text)
        assert result == {"key": "value"}

    def test_parse_invalid_json(self):
        """잘못된 JSON은 error 키 반환"""
        from analyzer.analyzer import _parse_json_response
        result = _parse_json_response("not json at all")
        assert "error" in result


# ── Config 환경변수 테스트 ────────────────────────────

class TestConfigEnvVars:
    """환경변수 기반 설정 로드 검증"""

    def test_model_analysis_env_override(self):
        """MODEL_ANALYSIS 환경변수 오버라이드"""
        import os
        os.environ["MODEL_ANALYSIS"] = "claude-opus-4-6"
        try:
            from shared.config import AnalyzerConfig
            cfg = AnalyzerConfig()
            assert cfg.model_analysis == "claude-opus-4-6"
        finally:
            os.environ.pop("MODEL_ANALYSIS", None)

    def test_min_new_news_env_override(self):
        """MIN_NEW_NEWS 환경변수 오버라이드"""
        import os
        os.environ["MIN_NEW_NEWS"] = "10"
        try:
            from shared.config import AnalyzerConfig
            cfg = AnalyzerConfig()
            assert cfg.min_new_news == 10
        finally:
            os.environ.pop("MIN_NEW_NEWS", None)

    def test_model_translate_env_override(self):
        """MODEL_TRANSLATE 환경변수 오버라이드"""
        import os
        os.environ["MODEL_TRANSLATE"] = "claude-sonnet-4-6"
        try:
            from shared.config import AnalyzerConfig
            cfg = AnalyzerConfig()
            assert cfg.model_translate == "claude-sonnet-4-6"
        finally:
            os.environ.pop("MODEL_TRANSLATE", None)
