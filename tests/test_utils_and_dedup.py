"""Unit tests for escape_ilike (src.api.utils) and _dedup_items (src.api.v1.context_pack)."""

from src.api.utils import escape_ilike
from src.api.v1.context_pack import _dedup_items


# ── escape_ilike tests ───────────────────────────────────────────────


def test_escape_ilike_no_special_chars():
    assert escape_ilike("hello world") == "hello world"


def test_escape_ilike_percent():
    assert escape_ilike("100%") == "100\\%"


def test_escape_ilike_underscore():
    assert escape_ilike("file_name") == "file\\_name"


def test_escape_ilike_backslash():
    assert escape_ilike("path\\to") == "path\\\\to"


def test_escape_ilike_combined():
    """Backslash must be escaped first to avoid double-escaping."""
    assert escape_ilike("a\\b%c_d") == "a\\\\b\\%c\\_d"


def test_escape_ilike_empty_string():
    assert escape_ilike("") == ""


# ── _dedup_items tests ───────────────────────────────────────────────


def test_dedup_empty_list():
    assert _dedup_items([]) == []


def test_dedup_no_duplicates():
    items = [
        {"url": "https://a.com/1", "relevance_score": 0.9, "cluster_id": None},
        {"url": "https://b.com/2", "relevance_score": 0.8, "cluster_id": None},
    ]
    result = _dedup_items(items)
    assert len(result) == 2
    assert result[0]["url"] == "https://a.com/1"
    assert result[1]["url"] == "https://b.com/2"


def test_dedup_cluster_keeps_highest_score():
    items = [
        {"url": "https://a.com/1", "relevance_score": 0.7, "cluster_id": "c1"},
        {"url": "https://b.com/2", "relevance_score": 0.9, "cluster_id": "c1"},
    ]
    result = _dedup_items(items)
    assert len(result) == 1
    assert result[0]["url"] == "https://b.com/2"


def test_dedup_url_base_dedup():
    items = [
        {"url": "https://a.com/page?q=1", "relevance_score": 0.6, "cluster_id": None},
        {
            "url": "https://a.com/page#section",
            "relevance_score": 0.8,
            "cluster_id": None,
        },
    ]
    result = _dedup_items(items)
    assert len(result) == 1
    assert result[0]["relevance_score"] == 0.8


def test_dedup_null_cluster_ids_pass_through():
    items = [
        {"url": "https://a.com/1", "relevance_score": 0.9, "cluster_id": None},
        {"url": "https://b.com/2", "relevance_score": 0.8, "cluster_id": None},
        {"url": "https://c.com/3", "relevance_score": 0.7, "cluster_id": None},
    ]
    result = _dedup_items(items)
    assert len(result) == 3


def test_dedup_combined_cluster_and_url():
    """Both cluster and URL dedup layers work together."""
    items = [
        # Cluster c1: two items, keep highest score
        {"url": "https://a.com/1", "relevance_score": 0.6, "cluster_id": "c1"},
        {"url": "https://b.com/2", "relevance_score": 0.9, "cluster_id": "c1"},
        # No cluster but same base URL: keep highest score
        {"url": "https://d.com/page?v=1", "relevance_score": 0.5, "cluster_id": None},
        {"url": "https://d.com/page?v=2", "relevance_score": 0.7, "cluster_id": None},
        # Unique item
        {"url": "https://e.com/unique", "relevance_score": 0.8, "cluster_id": None},
    ]
    result = _dedup_items(items)
    # Should have 3 items: cluster winner (b.com), URL winner (d.com?v=2), unique (e.com)
    assert len(result) == 3
    urls = [r["url"] for r in result]
    assert "https://b.com/2" in urls
    assert "https://e.com/unique" in urls
    # The URL dedup winner for d.com/page should be the one with score 0.7
    d_items = [r for r in result if "d.com" in r["url"]]
    assert len(d_items) == 1
    assert d_items[0]["relevance_score"] == 0.7
