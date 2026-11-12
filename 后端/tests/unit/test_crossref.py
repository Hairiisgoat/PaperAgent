"""Offline contract tests for the public scholarly metadata adapter."""
import json

import httpx
import pytest

from app.infrastructure.crossref import SearchUnavailable, search_crossref, title_matches_query


def test_search_parses_and_deduplicates_without_inventing_metadata():
    def handler(request):
        assert request.url.host == "api.crossref.org"
        assert request.url.params["query.bibliographic"] == "machine learning"
        item = {"DOI": "10.1234/Example", "title": ["A real <i>title</i>"],
                "author": [{"given": "A", "family": "Smith"}],
                "published": {"date-parts": [[2022]]}, "container-title": ["A journal"],
                "abstract": "<jats:p>Actual abstract</jats:p>"}
        return httpx.Response(200, json={"message": {"items": [item, {**item, "DOI": "10.1234/example"}, {"title": ["No DOI"]}]}})
    records = search_crossref("machine learning", transport=httpx.MockTransport(handler))
    assert len(records) == 1
    assert records[0].doi == "10.1234/example"
    assert records[0].title == "A real title"
    assert records[0].authors == ("A Smith",)
    assert records[0].year == 2022
    assert records[0].journal == "A journal"
    assert records[0].url == "https://doi.org/10.1234/Example"
    assert records[0].abstract == "Actual abstract"


@pytest.mark.parametrize("status,body", [(503, b"secret upstream"), (200, b"not JSON"), (200, json.dumps({"message": {"items": {}}}).encode())])
def test_errors_are_safe(status, body):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=body))
    with pytest.raises(SearchUnavailable) as caught:
        search_crossref("sensitive query", transport=transport)
    assert "sensitive" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_invalid_parameters_and_optional_fields():
    with pytest.raises(ValueError):
        search_crossref("", transport=httpx.MockTransport(lambda request: pytest.fail("no request")))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"message": {"items": [{"DOI": "10.1234/a", "title": ["Title"]}]}}))
    item = search_crossref("query", transport=transport)[0]
    assert item.year is None and item.journal is None and item.abstract is None and not item.authors


def test_title_filter_is_lexical_and_fails_closed():
    assert title_matches_query("Machine Learning in medicine", "Machine learning for clinical outcomes")
    assert title_matches_query("数字经济与区域发展", "数字经济的发展")
    assert not title_matches_query("Machine learning", "Marine biology")
    assert not title_matches_query("research review", "research review")
