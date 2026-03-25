"""Tests for src/api/search_utils.py — collapse_clusters function.

Phase 24: Cluster dedup in search results.
"""


class MockItem:
    """Lightweight mock with attribute access for cluster_id and scores."""

    def __init__(self, cluster_id=None, rank=0.0, name=""):
        self.cluster_id = cluster_id
        self.relevance_score = rank
        self.rank = rank
        self.name = name

    def __repr__(self):
        return f"MockItem({self.name!r}, cluster={self.cluster_id}, rank={self.rank})"


class MockRow:
    """Simulate SQLAlchemy Row with _mapping dict access."""

    def __init__(self, data: dict):
        self._mapping = data

    def __repr__(self):
        return f"MockRow({self._mapping.get('name', '')})"


from src.api.search_utils import collapse_clusters


class TestCollapseClusterBasic:
    def test_empty_list_returns_empty(self):
        assert collapse_clusters([]) == []

    def test_single_item_no_cluster(self):
        item = MockItem(cluster_id=None, rank=0.5, name="solo")
        result = collapse_clusters([item])
        assert len(result) == 1
        assert result[0] is item

    def test_single_item_with_cluster(self):
        item = MockItem(cluster_id="c1", rank=0.5, name="only")
        result = collapse_clusters([item])
        assert len(result) == 1
        assert result[0] is item


class TestCollapseClusterDedup:
    def test_five_items_same_cluster_collapse_to_one(self):
        """Spec from PLAN.md 24-01a: verify 5 items in same cluster collapse to 1."""
        items = [
            MockItem(cluster_id="c1", rank=0.9 - i * 0.1, name=f"item{i}")
            for i in range(5)
        ]
        result = collapse_clusters(items, rank_key="rank")
        assert len(result) == 1
        assert result[0].rank == 0.9  # highest rank kept

    def test_keeps_best_per_cluster(self):
        items = [
            MockItem(cluster_id="c1", rank=0.3, name="low"),
            MockItem(cluster_id="c1", rank=0.8, name="high"),
            MockItem(cluster_id="c1", rank=0.5, name="mid"),
        ]
        result = collapse_clusters(items, rank_key="rank")
        assert len(result) == 1
        assert result[0].name == "high"

    def test_multiple_clusters_keep_one_each(self):
        items = [
            MockItem(cluster_id="c1", rank=0.9, name="c1-best"),
            MockItem(cluster_id="c1", rank=0.3, name="c1-worse"),
            MockItem(cluster_id="c2", rank=0.7, name="c2-best"),
            MockItem(cluster_id="c2", rank=0.2, name="c2-worse"),
        ]
        result = collapse_clusters(items, rank_key="rank")
        assert len(result) == 2
        names = [r.name for r in result]
        assert "c1-best" in names
        assert "c2-best" in names


class TestCollapseClusterNulls:
    def test_null_cluster_ids_treated_as_unique(self):
        items = [
            MockItem(cluster_id=None, rank=0.5, name="a"),
            MockItem(cluster_id=None, rank=0.3, name="b"),
            MockItem(cluster_id=None, rank=0.8, name="c"),
        ]
        result = collapse_clusters(items)
        assert len(result) == 3  # all kept

    def test_mixed_null_and_clustered(self):
        items = [
            MockItem(cluster_id=None, rank=0.9, name="solo1"),
            MockItem(cluster_id="c1", rank=0.8, name="c1-best"),
            MockItem(cluster_id="c1", rank=0.3, name="c1-dup"),
            MockItem(cluster_id=None, rank=0.7, name="solo2"),
            MockItem(cluster_id="c2", rank=0.6, name="c2-only"),
        ]
        result = collapse_clusters(items, rank_key="rank")
        assert len(result) == 4  # solo1, c1-best, solo2, c2-only
        names = [r.name for r in result]
        assert "c1-dup" not in names


class TestCollapseClusterOrdering:
    def test_preserves_original_order(self):
        """Results should maintain SQL ordering, not re-sort."""
        items = [
            MockItem(cluster_id="c1", rank=0.9, name="first"),
            MockItem(cluster_id=None, rank=0.5, name="second"),
            MockItem(cluster_id="c2", rank=0.7, name="third"),
            MockItem(cluster_id="c1", rank=0.3, name="c1-dup"),
        ]
        result = collapse_clusters(items, rank_key="rank")
        names = [r.name for r in result]
        assert names == ["first", "second", "third"]

    def test_custom_rank_key(self):
        items = [
            MockItem(cluster_id="c1", rank=0.3, name="low-rank"),
            MockItem(cluster_id="c1", rank=0.9, name="high-rank"),
        ]
        # Use relevance_score as rank key (same value as rank in MockItem)
        result = collapse_clusters(items, rank_key="relevance_score")
        assert len(result) == 1
        assert result[0].name == "high-rank"


class TestCollapseClusterMappingAccess:
    def test_works_with_row_mapping(self):
        """SQLAlchemy rows use _mapping dict access, not attribute access."""
        items = [
            MockRow({"cluster_id": "c1", "rank": 0.9, "name": "best"}),
            MockRow({"cluster_id": "c1", "rank": 0.3, "name": "dup"}),
            MockRow({"cluster_id": None, "rank": 0.5, "name": "solo"}),
        ]
        result = collapse_clusters(items, rank_key="rank")
        assert len(result) == 2

    def test_works_with_missing_rank_key(self):
        """Items missing the rank key should get 0 as default."""
        items = [
            MockRow({"cluster_id": "c1", "rank": 0.5}),
            MockRow({"cluster_id": "c1"}),  # no rank key
        ]
        result = collapse_clusters(items, rank_key="rank")
        assert len(result) == 1


class TestCollapseClustersFeedPattern:
    """Tests verifying the feed endpoint's specific usage of collapse_clusters.

    The feed endpoint calls collapse_clusters(items, rank_key="relevance_score")
    on ORM objects. These tests confirm that pattern works correctly.
    """

    def test_collapse_clusters_keeps_best_per_cluster_by_relevance_score(self):
        """Feed endpoint collapses by relevance_score — verify best-per-cluster kept."""
        items = [
            MockItem(cluster_id="incident-123", rank=0.6, name="source-a"),
            MockItem(cluster_id="incident-123", rank=0.9, name="source-b"),
            MockItem(cluster_id="incident-123", rank=0.4, name="source-c"),
            MockItem(cluster_id=None, rank=0.7, name="unique-1"),
            MockItem(cluster_id="incident-456", rank=0.8, name="other-best"),
            MockItem(cluster_id="incident-456", rank=0.3, name="other-dup"),
            MockItem(cluster_id=None, rank=0.5, name="unique-2"),
        ]
        result = collapse_clusters(items, rank_key="relevance_score")

        # 2 clusters collapse to 1 each + 2 NULL items = 4 total
        assert len(result) == 4

        names = [r.name for r in result]
        # Best per cluster kept
        assert "source-b" in names  # highest relevance_score in incident-123
        assert "other-best" in names  # highest relevance_score in incident-456
        # Duplicates removed
        assert "source-a" not in names
        assert "source-c" not in names
        assert "other-dup" not in names
        # NULL cluster_id items all pass through
        assert "unique-1" in names
        assert "unique-2" in names

    def test_cluster_count_attribute_set_on_representatives(self):
        """Feed uses _cluster_count for UI — verify it's set correctly."""
        items = [
            MockItem(cluster_id="c1", rank=0.9, name="best"),
            MockItem(cluster_id="c1", rank=0.5, name="dup1"),
            MockItem(cluster_id="c1", rank=0.3, name="dup2"),
            MockItem(cluster_id=None, rank=0.7, name="solo"),
        ]
        result = collapse_clusters(items, rank_key="relevance_score")

        assert len(result) == 2
        cluster_rep = [r for r in result if r.name == "best"][0]
        solo_item = [r for r in result if r.name == "solo"][0]

        assert cluster_rep._cluster_count == 3  # 3 items in cluster
        assert solo_item._cluster_count == 1  # ungrouped
