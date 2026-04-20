"""Jinja2 커스텀 필터 — pages.py에서 추출 (B1).

main.py에서 `register(env)`를 호출해 모든 템플릿 환경에 등록한다.
"""
import re
import markdown as _markdown
import bleach
from markupsafe import Markup
from jinja2 import Environment


def nl_numbered(text: str) -> Markup:
    """①②③ 또는 1. 2. 3. 형태의 번호 리스트를 줄바꿈으로 분리."""
    if not text:
        return Markup("")
    parts = re.split(r'\s*(?=[①-⑳])', text)
    if len(parts) > 1:
        stripped = [p.strip() for p in parts if p.strip()]
        return Markup('<br>'.join(stripped))
    return Markup(re.sub(r'(?<=\S)\s+(\d+)\.\s', r'<br>\1. ', text))


_CURRENCY_SYMBOLS = {"KRW": "₩", "USD": "$", "EUR": "€", "JPY": "¥", "GBP": "£", "CNY": "¥"}


def fmt_price(value, currency: str = "") -> str:
    """가격을 통화 기호 + 천 단위 쉼표로 포맷팅 (정수 통화는 소수점 제거)."""
    if value is None:
        return "-"
    try:
        num = float(value)
    except (ValueError, TypeError):
        return str(value)
    if num == 0:
        return "-"
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper(), "")
    # KRW, JPY 등은 소수점 없이 표시
    if (currency or "").upper() in ("KRW", "JPY"):
        return f"{symbol}{num:,.0f}"
    return f"{symbol}{num:,.2f}"


_MD_ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "em", "b", "i", "u", "s", "del", "mark", "sub", "sup",
    "blockquote", "code", "pre",
    "ul", "ol", "li",
    "a",
    "table", "thead", "tbody", "tr", "th", "td",
    "img",
    "span", "div",
]
_MD_ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title"],
    "th": ["align", "colspan", "rowspan"],
    "td": ["align", "colspan", "rowspan"],
    "code": ["class"],
    "pre": ["class"],
    "span": ["class"],
    "div": ["class"],
}
_MD_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def markdown_to_html(text) -> Markup:
    """AI가 생성한 마크다운 원문을 sanitize된 HTML로 렌더링.

    - extensions: tables(|표|), fenced_code(```), nl2br(줄바꿈→<br>), sane_lists
    - bleach로 화이트리스트 태그/속성만 허용 → XSS 방지
    - 모든 링크에 target="_blank" + rel="noopener" 부여
    """
    if not text:
        return Markup("")
    html = _markdown.markdown(
        str(text),
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
        output_format="html",
    )
    cleaned = bleach.clean(
        html,
        tags=_MD_ALLOWED_TAGS,
        attributes=_MD_ALLOWED_ATTRS,
        protocols=_MD_ALLOWED_PROTOCOLS,
        strip=True,
    )
    cleaned = re.sub(
        r'<a\s+([^>]*?)>',
        lambda m: f'<a {m.group(1)} target="_blank" rel="noopener noreferrer">',
        cleaned,
    )
    return Markup(cleaned)


def register(env: Environment) -> None:
    """Jinja2 환경에 모든 커스텀 필터를 등록."""
    env.filters["nl_numbered"] = nl_numbered
    env.filters["fmt_price"] = fmt_price
    env.filters["markdown_to_html"] = markdown_to_html
