"""단일 Jinja2Templates 인스턴스 — 모든 라우트가 공유 (B2)."""
from fastapi.templating import Jinja2Templates
from api.template_filters import register

templates = Jinja2Templates(directory="api/templates")
register(templates.env)
