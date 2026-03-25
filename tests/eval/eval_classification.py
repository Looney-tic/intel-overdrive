#!/usr/bin/env python3
"""
Recall measurement script for the Intel pipeline classification system.

Loads eval_set.json, classifies each item using the same LLMClient and
CLASSIFICATION_SYSTEM_PROMPT as the production pipeline, then computes
per-type and overall recall.

REQUIRES: Real ANTHROPIC_API_KEY and running Redis (for SpendTracker).

Usage:
    python3 tests/eval/eval_classification.py [--limit N]

Exit codes:
    0 -- overall recall >= 90%
    1 -- overall recall < 90% (classification gate not met)
    2 -- configuration/setup error
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

# Allow running from any directory
_project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_project_root))

from src.core.config import get_settings
from src.services.llm_client import LLMClient
from src.services.spend_tracker import SpendTracker, SpendLimitExceeded
from src.workers.pipeline_workers import (
    CLASSIFICATION_SYSTEM_PROMPT,
    VALID_PRIMARY_TYPES,
)

EVAL_SET_PATH = Path(__file__).parent / "eval_set.json"
RESULTS_PATH = Path(__file__).parent / "eval_results.json"
RECALL_THRESHOLD = 0.90
PROGRESS_INTERVAL = 20


def load_eval_set() -> list:
    """Load and validate the eval set from disk."""
    if not EVAL_SET_PATH.exists():
        print(f"ERROR: eval_set.json not found at {EVAL_SET_PATH}", file=sys.stderr)
        sys.exit(2)
    with open(EVAL_SET_PATH) as f:
        data = json.load(f)
    if not data:
        print("ERROR: eval_set.json is empty", file=sys.stderr)
        sys.exit(2)
    return data


def check_disjointness(eval_data: list) -> None:
    """Verify eval set is disjoint from the reference set."""
    try:
        from scripts.seed_reference_set import REFERENCE_ITEMS  # noqa: PLC0415

        ref_urls = {item["url"] for item in REFERENCE_ITEMS}
        eval_urls = {item["url"] for item in eval_data}
        overlap = ref_urls & eval_urls
        if overlap:
            print(
                f"WARNING: {len(overlap)} URLs overlap between eval set and reference set:",
                file=sys.stderr,
            )
            for url in sorted(overlap):
                print(f"  {url}", file=sys.stderr)
        else:
            print("Disjointness check: OK (0 overlapping URLs with reference set)")
    except ImportError:
        print(
            "WARNING: Could not import REFERENCE_ITEMS from seed script -- "
            "skipping disjointness check",
            file=sys.stderr,
        )


async def classify_item(
    llm_client: LLMClient,
    item: dict,
) -> tuple:
    """Classify a single eval item. Returns (predicted_type, confidence)."""
    title = item.get("title", "")
    content = item.get("content", "")

    # Build input in the same format as the production pipeline worker
    classification_input = f"Title: {title}\nContent: {content[:2000]}"

    try:
        result = await llm_client.classify(
            classification_input, CLASSIFICATION_SYSTEM_PROMPT
        )
        predicted_type = result.primary_type
        confidence = result.confidence

        # Validate against taxonomy -- invalid type counts as classification error
        if predicted_type not in VALID_PRIMARY_TYPES:
            return "unknown", 0.0

        return predicted_type, confidence

    except SpendLimitExceeded:
        raise  # Propagate to caller for graceful shutdown
    except Exception as exc:
        # Log error but count as incorrect
        print(f"  ERROR classifying {item['url'][:60]}: {exc}", file=sys.stderr)
        return "error", 0.0


async def run_eval(eval_data: list, limit=None) -> dict:
    """Run classification over the eval set. Returns detailed results dict."""
    settings = get_settings()

    import redis.asyncio as aioredis

    try:
        redis_client = aioredis.from_url(settings.REDIS_URL)
        # Quick ping to verify connection
        await redis_client.ping()
    except Exception as exc:
        print(
            f"ERROR: Cannot connect to Redis at {settings.REDIS_URL}: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)

    spend_tracker = SpendTracker(redis_client)
    llm_client = LLMClient(spend_tracker)

    if limit:
        eval_data = eval_data[:limit]

    total = len(eval_data)
    results = []
    correct_by_type = Counter()
    total_by_type = Counter()
    confusion = Counter()  # (expected, predicted) pairs

    print(f"\nClassifying {total} items using {settings.LLM_MODEL}...")
    print(f"Recall threshold: {RECALL_THRESHOLD:.0%}\n")

    spend_blocked = False

    for i, item in enumerate(eval_data):
        expected = item["expected_type"]
        total_by_type[expected] += 1

        try:
            predicted, confidence = await classify_item(llm_client, item)
        except SpendLimitExceeded:
            print(f"\nSpend limit reached at item {i + 1}/{total} -- stopping early")
            spend_blocked = True
            break

        is_correct = predicted == expected
        if is_correct:
            correct_by_type[expected] += 1
        else:
            confusion[(expected, predicted)] += 1

        results.append(
            {
                "url": item["url"],
                "title": item["title"],
                "expected_type": expected,
                "predicted_type": predicted,
                "confidence": confidence,
                "correct": is_correct,
            }
        )

        if (i + 1) % PROGRESS_INTERVAL == 0 or (i + 1) == total:
            processed = i + 1
            running_correct = sum(correct_by_type.values())
            running_recall = running_correct / processed if processed > 0 else 0.0
            print(f"  [{processed:3d}/{total}] running recall: {running_recall:.1%}")

    await redis_client.aclose()

    # Compute final metrics
    items_processed = len(results)
    total_correct = sum(correct_by_type.values())
    overall_recall = total_correct / items_processed if items_processed > 0 else 0.0

    per_type_recall = {}
    for t in VALID_PRIMARY_TYPES:
        n = total_by_type[t]
        c = correct_by_type[t]
        per_type_recall[t] = c / n if n > 0 else 0.0

    top_confusions = confusion.most_common(10)

    return {
        "summary": {
            "total_items": total,
            "items_processed": items_processed,
            "total_correct": total_correct,
            "overall_recall": overall_recall,
            "pass": overall_recall >= RECALL_THRESHOLD,
            "spend_blocked": spend_blocked,
            "model": settings.LLM_MODEL,
        },
        "per_type_recall": per_type_recall,
        "per_type_counts": {
            t: {"correct": correct_by_type[t], "total": total_by_type[t]}
            for t in VALID_PRIMARY_TYPES
        },
        "top_confusions": [
            {"expected": exp, "predicted": pred, "count": cnt}
            for (exp, pred), cnt in top_confusions
        ],
        "items": results,
    }


def print_report(results: dict) -> None:
    """Print a human-readable classification report."""
    summary = results["summary"]
    per_type = results["per_type_recall"]
    counts = results["per_type_counts"]
    confusions = results["top_confusions"]

    print("\n" + "=" * 60)
    print("CLASSIFICATION RECALL REPORT")
    print("=" * 60)
    print(f"Model:           {summary['model']}")
    print(f"Items processed: {summary['items_processed']} / {summary['total_items']}")
    print(f"Total correct:   {summary['total_correct']}")
    print(f"Overall recall:  {summary['overall_recall']:.1%}")
    print(f"Threshold:       {RECALL_THRESHOLD:.0%}")
    status = "PASS" if summary["pass"] else "FAIL"
    print(f"Status:          {status}")
    if summary["spend_blocked"]:
        print("NOTE: Evaluation terminated early due to spend limit")

    print("\n--- Per-Type Recall ---")
    print(f"{'Type':<12} {'Recall':>8} {'Correct':>8} {'Total':>8}")
    print("-" * 42)
    for t in sorted(VALID_PRIMARY_TYPES):
        recall = per_type.get(t, 0.0)
        c = counts.get(t, {}).get("correct", 0)
        n = counts.get(t, {}).get("total", 0)
        flag = " <-- WEAK" if recall < RECALL_THRESHOLD else ""
        print(f"{t:<12} {recall:>7.1%} {c:>8} {n:>8}{flag}")

    if confusions:
        print("\n--- Top Misclassifications ---")
        print(f"{'Expected':<12} {'Predicted':<12} {'Count':>6}")
        print("-" * 34)
        for row in confusions:
            print(f"{row['expected']:<12} {row['predicted']:<12} {row['count']:>6}")

    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure classification recall on eval set"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of items to classify (for testing)",
    )
    args = parser.parse_args()

    eval_data = load_eval_set()
    check_disjointness(eval_data)

    results = asyncio.run(run_eval(eval_data, limit=args.limit))

    print_report(results)

    # Save detailed results for debugging
    with open(RESULTS_PATH, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results saved to: {RESULTS_PATH}")

    # Exit with code 0 on pass, 1 on fail
    sys.exit(0 if results["summary"]["pass"] else 1)


if __name__ == "__main__":
    main()
