"""Bounded, read-only access to Crossref scholarly metadata.

Crossref search ranking is not a determination of relevance or quality. This
adapter does not assert that a result supports a claim or has been peer reviewed.
"""
from dataclasses import dataclass
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import quote

import httpx


class SearchUnavailable(Exception):
    """Search failed without exposing the query or upstream response."""


@dataclass(frozen=True)
class ScholarlyRecord:
    doi: str
    title: str
    authors: tuple[str, ...]
    year: int | None
    journal: str | None
    url: str
    abstract: str | None


_DOI = re.compile(r"^10\.\d{4,9}/[^\s<>\"']+$", re.IGNORECASE)
_TERMS = re.compile(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}")
_STOPWORDS = {"the", "and", "for", "with", "from", "using", "study", "research", "analysis", "review", "based", "task", "研究", "分析", "论文", "任务", "文献", "资料", "综述"}


def _terms(text: str) -> set[str]:
    value = unicodedata.normalize("NFKC", text).casefold()
    result: set[str] = set()
    for token in _TERMS.findall(value):
        if token in _STOPWORDS:
            continue
        if "\u4e00" <= token[0] <= "\u9fff":
            result.update(token[index:index + 2] for index in range(len(token) - 1)
                          if token[index:index + 2] not in _STOPWORDS)
        else:
            result.add(token)
    return result


def title_matches_query(query: str, title: str) -> bool:
    """Conservative lexical screen only; not a relevance or quality judgment."""
    return bool(_terms(query) & _terms(title))


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or len(value) > 100_000:
        return None
    parser = _Text()
    parser.feed(value)
    text = " ".join(" ".join(parser.parts).split())
    return text[:limit] or None


def _first(value: object, limit: int) -> str | None:
    return _plain(value[0], limit) if isinstance(value, list) and value else None


def _record(value: object) -> ScholarlyRecord | None:
    if not isinstance(value, dict):
        return None
    doi = value.get("DOI")
    title = _first(value.get("title"), 1000)
    if not isinstance(doi, str) or len(doi) > 255 or not _DOI.fullmatch(doi) or not title:
        return None
    authors: list[str] = []
    raw_authors = value.get("author")
    if isinstance(raw_authors, list):
        for author in raw_authors[:100]:
            if isinstance(author, dict):
                name = " ".join(filter(None, (_plain(author.get("given"), 100), _plain(author.get("family"), 100))))
                name = name or _plain(author.get("name"), 200)
                if name:
                    authors.append(name)
    year = None
    published = value.get("published")
    if isinstance(published, dict):
        parts = published.get("date-parts")
        if (isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]
                and type(parts[0][0]) is int and 1000 <= parts[0][0] <= 9999):
            year = parts[0][0]
    return ScholarlyRecord(
        doi=doi.casefold(), title=title, authors=tuple(authors), year=year,
        journal=_first(value.get("container-title"), 500),
        url="https://doi.org/" + quote(doi, safe="/"),
        abstract=_plain(value.get("abstract"), 5000),
    )


def search_crossref(query: str, *, limit: int = 10, transport: httpx.BaseTransport | None = None) -> list[ScholarlyRecord]:
    """Return deduplicated DOI-bearing records for one explicit search query.

    No retries; query content is not included in error messages. A caller owns
    authorization and must only pass information approved for external search.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 300 or not 1 <= limit <= 20:
        raise ValueError("Invalid search parameters")
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), transport=transport,
                          headers={"User-Agent": "PaperAgent/1.0 (scholarly metadata search)"}, follow_redirects=False) as client:
            response = client.get("https://api.crossref.org/works", params={"query.bibliographic": query, "rows": limit, "filter": "type:journal-article"})
            response.raise_for_status()
            payload = response.json()
        items = payload["message"]["items"]
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("Invalid response")
        records: list[ScholarlyRecord] = []
        seen: set[str] = set()
        for item in items:
            record = _record(item)
            if record is not None and record.doi not in seen:
                records.append(record)
                seen.add(record.doi)
        return records
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        # Raise outside the except block to avoid retaining sensitive request data.
        pass
    raise SearchUnavailable("Scholarly search unavailable")
