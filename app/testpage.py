"""기능 테스트 페이지. GET /test"""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()
_HTML = Path(__file__).parent / "static" / "test.html"


@router.get("/test", response_class=HTMLResponse)
def test_page():
    return HTMLResponse(_HTML.read_text(encoding="utf-8"))
