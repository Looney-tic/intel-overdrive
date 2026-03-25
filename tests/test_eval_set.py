"""
Data quality tests for the labeled evaluation set (tests/eval/eval_set.json).

These tests run without API keys and verify the structural integrity and
labeling quality of the eval dataset before it is used for recall measurement.
"""

import json
from pathlib import Path

import pytest

EVAL_SET_PATH = Path(__file__).parent / "eval" / "eval_set.json"
VALID_PRIMARY_TYPES = {"skill", "tool", "update", "practice", "docs"}
REQUIRED_FIELDS = {"url", "title", "content", "expected_type", "expected_tags"}
MIN_ITEMS_PER_TYPE = 30  # allows minor imbalance while still covering all types


@pytest.fixture(scope="module")
def eval_data() -> list[dict]:
    """Load and cache eval set for all tests in this module."""
    assert EVAL_SET_PATH.exists(), f"eval_set.json not found at {EVAL_SET_PATH}"
    with open(EVAL_SET_PATH) as f:
        return json.load(f)


def test_eval_set_count(eval_data: list[dict]) -> None:
    """Eval set must contain exactly 200 items."""
    assert len(eval_data) == 200, (
        f"Expected 200 items, got {len(eval_data)}. "
        "Add or remove items to reach the required count."
    )


def test_eval_set_type_distribution(eval_data: list[dict]) -> None:
    """All 5 primary types must be present with at least 30 items each."""
    from collections import Counter

    type_counts = Counter(item["expected_type"] for item in eval_data)

    # All 5 types must appear
    missing_types = VALID_PRIMARY_TYPES - set(type_counts.keys())
    assert not missing_types, f"Missing types: {missing_types}"

    # Each type must have sufficient representation
    weak_types = {t: c for t, c in type_counts.items() if c < MIN_ITEMS_PER_TYPE}
    assert (
        not weak_types
    ), f"Types with fewer than {MIN_ITEMS_PER_TYPE} items: {weak_types}"


def test_eval_set_required_fields(eval_data: list[dict]) -> None:
    """Every item must have all required fields."""
    problems = []
    for i, item in enumerate(eval_data):
        missing = REQUIRED_FIELDS - set(item.keys())
        if missing:
            problems.append(
                f"Item {i} ({item.get('url', 'NO_URL')}): missing {missing}"
            )
    assert not problems, f"Items with missing fields:\n" + "\n".join(problems[:10])


def test_eval_set_valid_types(eval_data: list[dict]) -> None:
    """All expected_type values must be from the valid taxonomy."""
    invalid = [
        (i, item.get("url", "NO_URL"), item["expected_type"])
        for i, item in enumerate(eval_data)
        if item.get("expected_type") not in VALID_PRIMARY_TYPES
    ]
    assert not invalid, f"Items with invalid expected_type:\n" + "\n".join(
        f"  Item {i}: '{t}' at {url}" for i, url, t in invalid[:10]
    )


def test_eval_set_unique_urls(eval_data: list[dict]) -> None:
    """All URLs must be unique within the eval set."""
    from collections import Counter

    url_counts = Counter(item["url"] for item in eval_data)
    duplicates = {url: count for url, count in url_counts.items() if count > 1}
    assert not duplicates, f"Duplicate URLs found:\n" + "\n".join(
        f"  {url} (x{count})" for url, count in list(duplicates.items())[:10]
    )


def test_eval_set_nonempty_content(eval_data: list[dict]) -> None:
    """All items must have non-empty title and content."""
    problems = []
    for i, item in enumerate(eval_data):
        url = item.get("url", "NO_URL")
        if not item.get("title", "").strip():
            problems.append(f"Item {i} ({url}): empty title")
        if not item.get("content", "").strip():
            problems.append(f"Item {i} ({url}): empty content")
    assert not problems, f"Items with empty title/content:\n" + "\n".join(problems[:10])


def test_eval_set_disjoint_from_reference(eval_data: list[dict]) -> None:
    """Eval set URLs must not overlap with the reference set."""
    try:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from scripts.seed_reference_set import REFERENCE_ITEMS  # noqa: PLC0415
    except ImportError:
        pytest.skip(
            "Cannot import REFERENCE_ITEMS from seed script — skipping disjointness check"
        )
        return

    ref_urls = {item["url"] for item in REFERENCE_ITEMS}
    eval_urls = {item["url"] for item in eval_data}
    overlap = ref_urls & eval_urls

    assert not overlap, (
        f"Eval set has {len(overlap)} URLs that also appear in the reference set.\n"
        f"Overlapping URLs:\n" + "\n".join(f"  {u}" for u in sorted(overlap)[:10])
    )


def test_eval_set_tags_are_lists(eval_data: list[dict]) -> None:
    """All expected_tags must be lists of non-empty strings."""
    problems = []
    for i, item in enumerate(eval_data):
        url = item.get("url", "NO_URL")
        tags = item.get("expected_tags")
        if not isinstance(tags, list):
            problems.append(
                f"Item {i} ({url}): expected_tags is {type(tags).__name__}, not list"
            )
            continue
        if not tags:
            problems.append(f"Item {i} ({url}): expected_tags is empty list")
            continue
        bad_tags = [t for t in tags if not isinstance(t, str) or not t.strip()]
        if bad_tags:
            problems.append(f"Item {i} ({url}): non-string or empty tags: {bad_tags}")

    assert not problems, f"Items with invalid expected_tags:\n" + "\n".join(
        problems[:10]
    )
