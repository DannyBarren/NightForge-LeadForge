"""LangChain + CrewAI tools: free-tier search, page fetch, Google Sheets export."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from leadforge.config_loader import AppConfig, get_config
logger = logging.getLogger(__name__)

USER_AGENT = (
    "LeadForge-Bot/0.1 (+https://github.com/DannyBarren/NightForge-LeadForge; "
    "public-data research only)"
)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _strip_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _duckduckgo_search(query: str, max_results: int = 5) -> str:
    """Primary DDG path via duckduckgo-search (no API key)."""
    from duckduckgo_search import DDGS

    lines: list[str] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            lines.append(
                f"- {r.get('title', 'N/A')}\n  {r.get('href', r.get('link', ''))}\n  {r.get('body', '')[:400]}"
            )
    return "\n".join(lines) if lines else "No DuckDuckGo results."


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _tavily_search(query: str, max_results: int) -> str:
    from tavily import TavilyClient

    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    resp = client.search(query=query, max_results=max_results)
    lines = []
    for r in resp.get("results", []):
        lines.append(
            f"- {r.get('title', 'N/A')}\n  {r.get('url', '')}\n  {r.get('content', '')[:400]}"
        )
    return "\n".join(lines) if lines else "No Tavily results."


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _brave_search(query: str, count: int) -> str:
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
    }
    with httpx.Client(timeout=20.0) as client:
        r = client.get(url, headers=headers, params={"q": query, "count": count})
        r.raise_for_status()
        data = r.json()
    web = data.get("web", {}).get("results", [])
    lines = [
        f"- {item.get('title')}\n  {item.get('url')}\n  {item.get('description', '')}"
        for item in web
    ]
    return "\n".join(lines) if lines else "No Brave results."


class SearchInput(BaseModel):
    query: str = Field(..., description="Web search query string")


class UnifiedWebSearchTool(BaseTool):
    """
    Single search tool for agents — tries Tavily → Brave → DuckDuckGo automatically.
    Reduces agent confusion and failed tool picks.
    """

    name: str = "web_search"
    description: str = (
        "Search the public web for businesses, reviews, and news. "
        "Input: a concise search query string. Returns titles, URLs, and snippets."
    )
    args_schema: type[BaseModel] = SearchInput
    max_results: int = 5
    prefer: str = "tavily"

    def _run(self, query: str) -> str:
        order = ["tavily", "brave", "duckduckgo"]
        if self.prefer in order:
            order.remove(self.prefer)
            order.insert(0, self.prefer)

        errors: list[str] = []
        for backend in order:
            try:
                if backend == "tavily" and os.getenv("TAVILY_API_KEY"):
                    return _tavily_search(query, self.max_results)
                if backend == "brave" and os.getenv("BRAVE_API_KEY"):
                    return _brave_search(query, self.max_results)
                if backend == "duckduckgo":
                    return _duckduckgo_search(query, self.max_results)
            except Exception as e:
                logger.warning("%s search failed: %s", backend, e)
                errors.append(f"{backend}: {e}")

        return "All search backends failed. " + "; ".join(errors)


class FetchPageInput(BaseModel):
    url: str = Field(..., description="Public HTTPS URL to fetch")


class FetchPublicPageTool(BaseTool):
    name: str = "fetch_public_page"
    description: str = (
        "Fetch text from a public web page (single GET, no login). "
        "Use for company websites or public review pages. Input: full URL."
    )
    args_schema: type[BaseModel] = FetchPageInput
    timeout_sec: int = 15
    max_chars: int = 12000

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=5))
    def _run(self, url: str) -> str:
        from urllib.parse import urlparse

        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urlparse(url)
        if not parsed.netloc:
            return "Invalid URL."

        with httpx.Client(
            timeout=self.timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type.lower():
            return _truncate(resp.text, self.max_chars)
        return _truncate(_strip_html(resp.text), self.max_chars)


class SheetsAppendInput(BaseModel):
    rows_json: str = Field(..., description="JSON array of export row dicts")


class GoogleSheetsAppendTool(BaseTool):
    name: str = "append_google_sheet"
    description: str = "Append JSON rows to Google Sheets (optional)."
    args_schema: type[BaseModel] = SheetsAppendInput

    def _run(self, rows_json: str) -> str:
        creds_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON")
        sheet_id = os.getenv("GOOGLE_SHEET_ID")
        worksheet = os.getenv("GOOGLE_SHEET_WORKSHEET", "LeadForge Output")
        if not creds_path or not sheet_id:
            return "Google Sheets not configured."
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(sheet_id)
            try:
                ws = sh.worksheet(worksheet)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=worksheet, rows=1000, cols=20)

            rows = json.loads(rows_json)
            if not rows:
                return "No rows."
            headers = list(rows[0].keys())
            if not ws.get_all_values():
                ws.append_row(headers)
            for row in rows:
                ws.append_row([row.get(h, "") for h in headers])
            return f"Appended {len(rows)} rows."
        except Exception as e:
            logger.warning("Sheets error: %s", e)
            return f"Sheets error: {e}"


def build_search_tools(cfg: Optional[AppConfig] = None) -> list[BaseTool]:
    cfg = cfg or get_config()
    return [
        UnifiedWebSearchTool(
            max_results=cfg.search.get("max_results_per_query", 5),
            prefer=cfg.search.get("prefer", "tavily"),
        )
    ]


def build_research_tools(cfg: Optional[AppConfig] = None) -> list[BaseTool]:
    cfg = cfg or get_config()
    return build_search_tools(cfg) + [
        FetchPublicPageTool(
            timeout_sec=cfg.search.get("page_fetch_timeout_sec", 15),
            max_chars=cfg.search.get("page_max_chars", 12000),
        )
    ]


def build_all_tools(cfg: Optional[AppConfig] = None) -> list[BaseTool]:
    return build_research_tools(cfg) + [GoogleSheetsAppendTool()]
