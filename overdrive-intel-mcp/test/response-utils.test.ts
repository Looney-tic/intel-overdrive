/**
 * Tests for MCP response utility functions (Phase 24-03).
 *
 * Run: cd overdrive-intel-mcp && npx tsx --test test/response-utils.test.ts
 */
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  cleanItem,
  cleanItems,
  computeResultQuality,
  generateTldr,
  formatItemMarkdown,
  formatAsMarkdown,
  STRIP_FIELDS,
  MAX_RESPONSE_CHARS,
  MAX_BRIEFING_CHARS,
} from "../src/response-utils.js";

// ---------------------------------------------------------------------------
// cleanItem
// ---------------------------------------------------------------------------

describe("cleanItem", () => {
  it("strips redundant fields", () => {
    const item = {
      id: "abc-123",
      title: "Test",
      quality_score: 0.8,
      quality_score_details: { stars: 100 },
      confidence_score: 0.9,
      cluster_id: "c1",
      contrarian_signals: [],
      status: "processed",
      source_id: "src-1",
    };
    const { cleaned, id } = cleanItem(item);
    assert.equal(id, "abc-123");
    assert.equal(cleaned.title, "Test");
    for (const field of STRIP_FIELDS) {
      assert.equal(field in cleaned, false, `${field} should be stripped`);
    }
    // id should NOT be in cleaned (moved to footer)
    assert.equal("id" in cleaned, false);
  });

  it("drops excerpt when summary exists", () => {
    const { cleaned } = cleanItem({
      summary: "A good summary",
      excerpt: "An excerpt to drop",
    });
    assert.equal(cleaned.summary, "A good summary");
    assert.equal("excerpt" in cleaned, false);
  });

  it("keeps excerpt when no summary", () => {
    const { cleaned } = cleanItem({
      excerpt: "An excerpt to keep",
    });
    assert.equal(cleaned.excerpt, "An excerpt to keep");
  });

  it("shortens dates to YYYY-MM-DD", () => {
    const { cleaned } = cleanItem({
      created_at: "2026-03-22T17:00:00.000Z",
      published_at: "2026-03-20T10:30:00Z",
    });
    assert.equal(cleaned.created_at, "2026-03-22");
    assert.equal(cleaned.published_at, "2026-03-20");
  });

  it("does not shorten short dates", () => {
    const { cleaned } = cleanItem({
      created_at: "2026-03-22",
    });
    assert.equal(cleaned.created_at, "2026-03-22");
  });

  it("rounds relevance_score to 2 decimals", () => {
    const { cleaned } = cleanItem({
      relevance_score: 0.87654321,
      similarity: 0.12345678,
      rank: 0.555555,
    });
    assert.equal(cleaned.relevance_score, 0.88);
    assert.equal(cleaned.similarity, 0.12);
    assert.equal(cleaned.rank, 0.56);
  });

  it("preserves quality_score (no longer stripped)", () => {
    const { cleaned } = cleanItem({
      title: "A tool",
      quality_score: 0.85,
    });
    assert.equal(cleaned.quality_score, 0.85);
  });

  it("extracts github_stars from quality_score_details", () => {
    const { cleaned } = cleanItem({
      title: "Popular repo",
      quality_score_details: { signals: { stars: 2400 } },
    });
    assert.equal(cleaned.github_stars, 2400);
    // quality_score_details itself should be stripped
    assert.equal("quality_score_details" in cleaned, false);
  });

  it("does not add github_stars when stars is 0", () => {
    const { cleaned } = cleanItem({
      title: "No stars",
      quality_score_details: { signals: { stars: 0 } },
    });
    assert.equal("github_stars" in cleaned, false);
  });

  it("preserves non-special fields", () => {
    const { cleaned } = cleanItem({
      title: "My Title",
      url: "https://example.com",
      tags: ["a", "b"],
      primary_type: "tool",
    });
    assert.equal(cleaned.title, "My Title");
    assert.equal(cleaned.url, "https://example.com");
    assert.deepEqual(cleaned.tags, ["a", "b"]);
    assert.equal(cleaned.primary_type, "tool");
  });
});

// ---------------------------------------------------------------------------
// cleanItems
// ---------------------------------------------------------------------------

describe("cleanItems", () => {
  it("cleans array of items and collects IDs", () => {
    const items = [
      { id: "id-1", title: "A", quality_score: 0.5 },
      { id: "id-2", title: "B", status: "processed" },
    ];
    const { cleaned, ids } = cleanItems(items);
    assert.equal(cleaned.length, 2);
    assert.deepEqual(ids, ["id-1", "id-2"]);
    // Verify quality_score is preserved (no longer stripped) and rounded
    assert.equal((cleaned[0] as Record<string, unknown>).quality_score, 0.5);
    assert.equal("status" in (cleaned[1] as Record<string, unknown>), false);
  });

  it("passes through non-object items", () => {
    const items = ["a string", 42, null];
    const { cleaned, ids } = cleanItems(items as unknown[]);
    assert.deepEqual(cleaned, ["a string", 42, null]);
    assert.deepEqual(ids, []);
  });
});

// ---------------------------------------------------------------------------
// computeResultQuality
// ---------------------------------------------------------------------------

describe("computeResultQuality", () => {
  it("returns LOW for empty items", () => {
    assert.equal(computeResultQuality([]), "LOW");
  });

  it("returns LOW for items with no scores", () => {
    assert.equal(computeResultQuality([{ title: "no score" }]), "LOW");
  });

  it("returns LOW for single item with low score", () => {
    assert.equal(computeResultQuality([{ relevance_score: 0.2 }]), "LOW");
  });

  it("returns MEDIUM for 2+ items with moderate scores", () => {
    assert.equal(
      computeResultQuality([
        { relevance_score: 0.5 },
        { relevance_score: 0.4 },
      ]),
      "MEDIUM",
    );
  });

  it("returns HIGH for 3+ items with high avg relevance", () => {
    assert.equal(
      computeResultQuality([
        { relevance_score: 0.8 },
        { relevance_score: 0.75 },
        { relevance_score: 0.9 },
      ]),
      "HIGH",
    );
  });

  it("uses similarity score as fallback", () => {
    assert.equal(
      computeResultQuality([
        { similarity: 0.8 },
        { similarity: 0.75 },
        { similarity: 0.9 },
      ]),
      "HIGH",
    );
  });

  it("uses rank score as last fallback", () => {
    const result = computeResultQuality([{ rank: 0.5 }, { rank: 0.5 }]);
    assert.equal(result, "MEDIUM");
  });
});

// ---------------------------------------------------------------------------
// generateTldr
// ---------------------------------------------------------------------------

describe("generateTldr", () => {
  it("returns no-results message for empty items", () => {
    const tldr = generateTldr("test query", "search", []);
    assert.match(tldr, /No results found for "test query"/);
  });

  it("includes result count", () => {
    const items = [
      { title: "Item 1", summary: "A good summary about things and stuff" },
      { title: "Item 2" },
    ];
    const tldr = generateTldr("test", "search", items);
    assert.match(tldr, /Found 2 results/);
  });

  it("includes significance breakdown", () => {
    const items = [
      { title: "A", significance: "breaking", summary: "Something broke" },
      { title: "B", significance: "major" },
      { title: "C", significance: "breaking" },
    ];
    const tldr = generateTldr("test", "feed", items);
    assert.match(tldr, /2 breaking/);
    assert.match(tldr, /1 major/);
  });

  it("uses top summary for context", () => {
    const items = [
      { title: "Title", summary: "This is a detailed summary about the topic" },
    ];
    const tldr = generateTldr("test", "search", items);
    assert.match(tldr, /Found 1 result/);
    assert.match(tldr, /This is a detailed summary/);
  });

  it("uses title when summary is short", () => {
    const items = [{ title: "My Great Tool", summary: "Short" }];
    const tldr = generateTldr("test", "search", items);
    assert.match(tldr, /My Great Tool/);
  });

  it("singular for 1 result", () => {
    const tldr = generateTldr("q", "search", [
      { title: "T", summary: "A real summary here" },
    ]);
    assert.match(tldr, /1 result[^s]/);
  });
});

// ---------------------------------------------------------------------------
// formatItemMarkdown
// ---------------------------------------------------------------------------

describe("formatItemMarkdown", () => {
  it("includes title and index", () => {
    const md = formatItemMarkdown({ title: "My Tool" }, 1);
    assert.match(md, /1\. \*\*My Tool\*\*/);
  });

  it("includes type and significance tags", () => {
    const md = formatItemMarkdown(
      { title: "T", primary_type: "tool", significance: "breaking" },
      1,
    );
    assert.match(md, /\[tool, breaking\]/);
  });

  it("truncates long summaries at 200 chars", () => {
    const longSummary = "A".repeat(300);
    const md = formatItemMarkdown({ title: "T", summary: longSummary }, 1);
    assert.ok(md.includes("..."));
    assert.ok(md.length < 400); // much less than 300 + metadata
  });

  it("includes tags, date, and url in meta line", () => {
    const md = formatItemMarkdown(
      {
        title: "T",
        tags: ["mcp", "tool"],
        created_at: "2026-03-22",
        url: "https://example.com",
      },
      1,
    );
    assert.match(md, /Tags: mcp, tool/);
    assert.match(md, /2026-03-22/);
    assert.match(md, /https:\/\/example\.com/);
  });

  it("falls back to Untitled", () => {
    const md = formatItemMarkdown({}, 1);
    assert.match(md, /\*\*Untitled\*\*/);
  });

  it("shows quality label for established items", () => {
    const md = formatItemMarkdown({ title: "T", quality_score: 0.85 }, 1);
    assert.match(md, /Quality: established/);
  });

  it("shows quality label for emerging items", () => {
    const md = formatItemMarkdown({ title: "T", quality_score: 0.55 }, 1);
    assert.match(md, /Quality: emerging/);
  });

  it("shows quality label for new/unverified items", () => {
    const md = formatItemMarkdown({ title: "T", quality_score: 0.3 }, 1);
    assert.match(md, /Quality: new\/unverified/);
  });

  it("shows formatted star count", () => {
    const md = formatItemMarkdown({ title: "T", github_stars: 2400 }, 1);
    assert.match(md, /2\.4k stars/);
  });

  it("shows raw star count under 1000", () => {
    const md = formatItemMarkdown({ title: "T", github_stars: 150 }, 1);
    assert.match(md, /150 stars/);
  });
});

// ---------------------------------------------------------------------------
// formatAsMarkdown
// ---------------------------------------------------------------------------

describe("formatAsMarkdown", () => {
  it("produces markdown header with query, type, count", () => {
    const output = {
      query: "MCP auth",
      type: "search",
      result_quality: "HIGH",
      tldr: "Found stuff",
      items: [{ title: "Item 1" }],
      item_ids: ["id-1"],
    };
    const md = formatAsMarkdown(output, "search");
    assert.match(md, /# Overdrive Intel: "MCP auth"/);
    assert.match(md, /\(search, 1 results\)/);
    assert.match(md, /Result quality: HIGH/);
    assert.match(md, /\*\*TL;DR:\*\* Found stuff/);
  });

  it("includes note when present", () => {
    const output = {
      query: "test",
      note: "No high-confidence results",
      items: [{ title: "T" }],
    };
    const md = formatAsMarkdown(output, "search");
    assert.match(md, /> No high-confidence results/);
  });

  it("renders item IDs in footer", () => {
    const output = {
      query: "q",
      items: [{ title: "T" }],
      item_ids: ["abc-123", "def-456"],
    };
    const md = formatAsMarkdown(output, "search");
    assert.match(md, /IDs: abc-123, def-456 \(for feedback\)/);
  });

  it("falls back to JSON for non-item output", () => {
    const output = { query: "q", type: "status" };
    const md = formatAsMarkdown(output, "status");
    // Should be JSON since no arrays/objects and no note
    assert.match(md, /"query"/);
  });

  it("uses 8K budget for regular, 12K for briefing", () => {
    assert.equal(MAX_RESPONSE_CHARS, 8000);
    assert.equal(MAX_BRIEFING_CHARS, 12000);
  });

  it("respects budget limit and adds truncation notice", () => {
    // Generate many items that would exceed 8K
    const manyItems = Array.from({ length: 100 }, (_, i) => ({
      title: `Item ${i}`,
      summary: "A".repeat(200),
      primary_type: "tool",
      tags: ["tag1", "tag2"],
      url: "https://example.com/long-url-path",
    }));
    const output = { query: "test", items: manyItems };
    const md = formatAsMarkdown(output, "search");
    assert.ok(md.length <= MAX_RESPONSE_CHARS + 500); // some slack for final lines
    assert.match(md, /more results — refine query/);
  });

  it("renders multiple sections", () => {
    const output = {
      query: "q",
      items: [{ title: "Feed Item" }],
      library: [{ title: "Library Item" }],
    };
    const md = formatAsMarkdown(output, "search");
    assert.match(md, /## items/);
    assert.match(md, /## library/);
  });
});
