"""Shared search utility functions used by search, feed, and similar endpoints."""


def collapse_clusters(items: list, rank_key: str = "relevance_score") -> list:
    """Collapse cluster duplicates — keep best representative per cluster_id.

    For each cluster with more than 1 item, keeps the item with the highest
    score (determined by rank_key). Items with NULL/None cluster_id are treated
    as unique (not grouped).

    Works with both ORM objects (attribute access) and row mappings (dict access).
    Returns items with _cluster_count attribute set on representatives.
    """
    clusters: dict[str, list] = {}
    ungrouped: list = []
    for item in items:
        # Support both ORM objects and plain dicts/mappings
        cid = getattr(item, "cluster_id", None)
        if cid is None and hasattr(item, "_mapping"):
            cid = item._mapping.get("cluster_id")
        if cid is None:
            ungrouped.append(item)
        else:
            clusters.setdefault(cid, []).append(item)

    # Build a set of best-representative IDs per cluster
    best_ids: set = set()
    cluster_counts: dict = {}
    for cid, cluster_items in clusters.items():
        # Keep the item with the highest rank score
        def _get_rank(i):
            val = getattr(i, rank_key, None)
            if val is None and hasattr(i, "_mapping"):
                val = i._mapping.get(rank_key)
            return val or 0

        best = max(cluster_items, key=_get_rank)
        best_ids.add(id(best))
        cluster_counts[id(best)] = len(cluster_items)

    # Walk the original item list to preserve SQL ordering
    result: list = []
    seen_clusters: set = set()
    for item in items:
        cid = getattr(item, "cluster_id", None)
        if cid is None and hasattr(item, "_mapping"):
            cid = item._mapping.get("cluster_id")
        if cid is None:
            try:
                item._cluster_count = 1
            except (AttributeError, TypeError):
                pass
            result.append(item)
        elif cid not in seen_clusters and id(item) in best_ids:
            try:
                item._cluster_count = cluster_counts.get(id(item), 1)
            except (AttributeError, TypeError):
                pass
            seen_clusters.add(cid)
            result.append(item)
        # Skip non-best items in clusters

    return result
