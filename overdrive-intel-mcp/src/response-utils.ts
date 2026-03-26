/**
 * Pure response utility functions for the MCP server.
 * Extracted for testability — no side effects, no I/O.
 */

// ---------------------------------------------------------------------------
// Field stripping
// ---------------------------------------------------------------------------

/** Fields to strip from every item — internal/redundant for MCP consumers */
export const STRIP_FIELDS = new Set([
  "quality_score_details",
  "confidence_score",
  "cluster_id",
  "contrarian_signals",
  "status",
  "source_id",
]);

/** Date fields to shorten to YYYY-MM-DD */
export const DATE_FIELDS = new Set(["created_at", "published_at"]);

/**
 * Clean a single item: drop redundant fields, shorten dates, round scores,
 * drop excerpt when summary exists, extract ID for footer.
 */
export function cleanItem(item: Record<string, unknown>): {
  cleaned: Record<string, unknown>;
  id: string | null;
} {
  const result: Record<string, unknown> = {};
  let itemId: string | null = null;

  // Extract stars from quality_score_details before it gets stripped
  const details = item.quality_score_details as
    | Record<string, unknown>
    | undefined;
  if (details && typeof details === "object") {
    const signals = details.signals as Record<string, unknown> | undefined;
    if (signals && typeof signals === "object") {
      const stars = signals.stars as number | undefined;
      if (typeof stars === "number" && stars > 0) {
        result["github_stars"] = stars;
      }
    }
  }

  for (const [key, value] of Object.entries(item)) {
    if (STRIP_FIELDS.has(key)) continue;
    if (key === "excerpt" && item.summary) continue;
    if (key === "id") {
      itemId = String(value);
      continue;
    }
    if (
      DATE_FIELDS.has(key) &&
      typeof value === "string" &&
      value.length > 10
    ) {
      result[key] = value.slice(0, 10);
      continue;
    }
    if (
      (key === "relevance_score" ||
        key === "similarity" ||
        key === "rank" ||
        key === "quality_score") &&
      typeof value === "number"
    ) {
      result[key] = Math.round(value * 100) / 100;
      continue;
    }
    result[key] = value;
  }

  return { cleaned: result, id: itemId };
}

/**
 * Clean all items in an array, collecting IDs for footer.
 */
export function cleanItems(items: unknown[]): {
  cleaned: unknown[];
  ids: string[];
} {
  const cleaned: unknown[] = [];
  const ids: string[] = [];

  for (const item of items) {
    if (item && typeof item === "object" && !Array.isArray(item)) {
      const { cleaned: c, id } = cleanItem(item as Record<string, unknown>);
      cleaned.push(c);
      if (id) ids.push(id);
    } else {
      cleaned.push(item);
    }
  }

  return { cleaned, ids };
}

// ---------------------------------------------------------------------------
// Result quality + TL;DR
// ---------------------------------------------------------------------------

export function computeResultQuality(
  items: Record<string, unknown>[],
): "HIGH" | "MEDIUM" | "LOW" {
  if (items.length === 0) return "LOW";

  // Status items have no scores — if pipeline_health exists, it's a valid status response
  if (items.length === 1 && items[0].pipeline_health !== undefined)
    return "HIGH";

  // Only use relevance_score, similarity, match_score — these are on a 0-1 scale.
  // Exclude `rank` (RRF reciprocal rank, 0.01-0.06) which is NOT on a 0-1 scale
  // and would always produce "LOW" quality even for strong matches.
  const scores = items
    .map(
      (i) =>
        (i.relevance_score as number | undefined) ??
        (i.similarity as number | undefined) ??
        (i.match_score as number | undefined),
    )
    .filter((s): s is number => typeof s === "number" && s > 0);

  // Items that matched the query but have no 0-1 score (e.g. RRF rank-only results)
  // are still valid matches — default to MEDIUM rather than LOW.
  if (scores.length === 0) {
    if (items.length >= 3) return "HIGH";
    if (items.length >= 1) return "MEDIUM";
    return "LOW";
  }

  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;

  if (avg >= 0.7 && items.length >= 3) return "HIGH";
  if (avg >= 0.4 || items.length >= 2) return "MEDIUM";
  return "LOW";
}

export function generateTldr(
  query: string,
  type: string,
  allItems: Record<string, unknown>[],
): string {
  if (allItems.length === 0) {
    return `No results found for "${query}".`;
  }

  const topItem = allItems[0];

  // Status responses have pipeline_health instead of title/summary
  if (topItem.pipeline_health !== undefined) {
    const health = topItem.pipeline_health as string;
    const total = topItem.total_sources ?? "?";
    const active = topItem.active_sources ?? "?";
    return `Pipeline ${health}. ${total} sources (${active} active).`;
  }

  const topTitle = (topItem.title as string) || "untitled";
  const topSummary = (topItem.summary as string) || "";

  const sigCounts: Record<string, number> = {};
  for (const item of allItems) {
    const sig = (item.significance as string) || "informational";
    sigCounts[sig] = (sigCounts[sig] || 0) + 1;
  }

  const sigParts: string[] = [];
  if (sigCounts["breaking"]) sigParts.push(`${sigCounts["breaking"]} breaking`);
  if (sigCounts["major"]) sigParts.push(`${sigCounts["major"]} major`);

  const countStr = `${allItems.length} result${allItems.length === 1 ? "" : "s"}`;
  const sigStr = sigParts.length > 0 ? ` (${sigParts.join(", ")})` : "";

  const context =
    topSummary.length > 10
      ? topSummary.slice(0, 120).replace(/\s+\S*$/, "") + "..."
      : topTitle;

  return `Found ${countStr}${sigStr}. Top: ${context}`;
}

// ---------------------------------------------------------------------------
// Query relevance check
// ---------------------------------------------------------------------------

/**
 * Check whether the top results are actually relevant to the query entity.
 * Returns true if at least one of the top 3 items mentions a query word
 * in its title, summary, or tags. Returns true (= relevant) for empty
 * queries or empty result sets to avoid false disclaimers.
 */
export function checkQueryRelevance(
  query: string,
  items: Record<string, unknown>[],
): boolean {
  if (!query || items.length === 0) return true;
  // Extract meaningful words from query (>= 3 chars, skip common words)
  const skipWords = new Set([
    "the",
    "for",
    "and",
    "with",
    "from",
    "that",
    "this",
    "sdk",
    "api",
    "breaking",
    "changes",
    "change",
  ]);
  const queryWords = query
    .toLowerCase()
    .split(/\s+/)
    .filter((w) => w.length >= 3 && !skipWords.has(w));
  if (queryWords.length === 0) return true;
  // Check if any query word appears in title, summary, or tags of top 3 items
  const topItems = items.slice(0, 3);
  for (const item of topItems) {
    const searchText = [
      (item.title as string) || "",
      (item.summary as string) || "",
      ...(Array.isArray(item.tags) ? item.tags.map(String) : []),
    ]
      .join(" ")
      .toLowerCase();
    for (const word of queryWords) {
      if (searchText.includes(word)) return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Markdown formatting
// ---------------------------------------------------------------------------

export function formatItemMarkdown(
  item: Record<string, unknown>,
  index: number,
): string {
  const title = (item.title as string) || "Untitled";
  const primaryType =
    (item.primary_type as string) || (item.entry_type as string) || "";
  const significance = (item.significance as string) || "";
  const sourceName = (item.source_name as string) || "";
  const summary =
    (item.summary as string) ||
    (item.excerpt as string) ||
    (item.tldr as string) ||
    "";
  const tags = Array.isArray(item.tags)
    ? (item.tags as string[]).join(", ")
    : "";
  const date =
    (item.created_at as string) || (item.published_at as string) || "";
  const url = item.url as string;
  const topicPath = (item.topic_path as string) || "";
  const staleness = (item.staleness_risk as string) || "";
  const relevance =
    item.relevance_score ??
    item.similarity ??
    item.match_score ??
    item.rank ??
    "";

  const typeTag = [primaryType, significance].filter(Boolean).join(", ");
  const relStr = typeof relevance === "number" ? ` | rel: ${relevance}` : "";

  let line = `${index}. **${title}**`;
  if (typeTag) line += ` [${typeTag}]`;
  if (sourceName) line += ` | via ${sourceName}`;
  line += "\n";
  if (summary) {
    const trimmedSummary =
      summary.length > 200
        ? summary.slice(0, 200).replace(/\s+\S*$/, "") + "..."
        : summary;
    line += `   ${trimmedSummary}\n`;
  }
  const meta: string[] = [];
  if (tags) meta.push(`Tags: ${tags}`);
  if (topicPath) meta.push(`Topic: ${topicPath}`);
  if (staleness) meta.push(`Staleness: ${staleness}`);
  if (date) meta.push(date);
  const qualityScore = item.quality_score as number | undefined;
  if (typeof qualityScore === "number") {
    const qualityLabel =
      qualityScore >= 0.8
        ? "established"
        : qualityScore >= 0.5
          ? "emerging"
          : "new/unverified";
    meta.push(`Quality: ${qualityLabel}`);
  }
  const githubStars = item.github_stars as number | undefined;
  if (typeof githubStars === "number" && githubStars > 0) {
    const formatted =
      githubStars >= 1000
        ? `${(githubStars / 1000).toFixed(1).replace(/\.0$/, "")}k`
        : String(githubStars);
    meta.push(`${formatted} stars`);
  }
  if (relStr) meta.push(relStr.trim().replace(/^\| /, ""));
  if (url) meta.push(url);
  if (meta.length > 0) line += `   ${meta.join(" | ")}\n`;

  return line;
}

export const MAX_RESPONSE_CHARS = 8000;
export const MAX_BRIEFING_CHARS = 12000;

export function formatAsMarkdown(
  output: Record<string, unknown>,
  type: string,
): string {
  const query = (output.query as string) || "";
  const resultQuality = (output.result_quality as string) || "";
  const tldr = (output.tldr as string) || "";
  const itemIds = (output.item_ids as string[]) || [];
  const note = output.note as string;

  const budget = type === "briefing" ? MAX_BRIEFING_CHARS : MAX_RESPONSE_CHARS;

  // Compressed briefing: render concise output for briefing type
  if (type === "briefing") {
    // Look for compressed_briefing in the first data section (API response)
    for (const [key, value] of Object.entries(output)) {
      if (
        key === "query" ||
        key === "type" ||
        key === "item_ids" ||
        key === "result_quality" ||
        key === "tldr" ||
        key === "note" ||
        key === "_tip"
      )
        continue;
      if (value && typeof value === "object" && !Array.isArray(value)) {
        const section = value as Record<string, unknown>;
        if (typeof section.compressed_briefing === "string") {
          const queryLabel = query ? `: "${query}"` : "";
          let md = `# Intel Overdrive Briefing${queryLabel}\n\n`;
          md += `${section.compressed_briefing}\n`;

          // Add key items if present
          const items = section.items as unknown[] | undefined;
          if (items && items.length > 0) {
            md += `\n## Key Items\n`;
            let idx = 1;
            for (const item of items) {
              if (item && typeof item === "object") {
                md += formatItemMarkdown(item as Record<string, unknown>, idx);
                idx++;
              }
            }
          }

          if (itemIds.length > 0) {
            md += `\n---\nIDs: ${itemIds.join(", ")} (for feedback)\n`;
          }
          return md;
        }
      }
    }
    // Fall through to standard rendering if no compressed_briefing found
  }

  const sections: Array<{ label: string; items: Record<string, unknown>[] }> =
    [];
  for (const [key, value] of Object.entries(output)) {
    if (
      key === "query" ||
      key === "type" ||
      key === "item_ids" ||
      key === "result_quality" ||
      key === "tldr" ||
      key === "note" ||
      key === "_tip"
    )
      continue;
    if (Array.isArray(value)) {
      sections.push({
        label: key,
        items: value as Record<string, unknown>[],
      });
    } else if (value && typeof value === "object") {
      sections.push({ label: key, items: [value as Record<string, unknown>] });
    }
  }

  if (sections.length === 0 && !note) {
    return JSON.stringify(output, null, 2).slice(0, budget);
  }

  const totalItems = sections.reduce((sum, s) => sum + s.items.length, 0);
  let md = `# Intel Overdrive: "${query}" (${type}, ${totalItems} results)\n`;
  if (resultQuality) md += `Result quality: ${resultQuality}\n`;
  md += "\n";
  if (tldr) md += `**TL;DR:** ${tldr}\n\n`;
  if (note) md += `> ${note}\n\n`;

  for (const section of sections) {
    md += `## ${section.label}\n`;

    // Status sections: render as key-value table instead of item list
    if (
      section.items.length === 1 &&
      section.items[0].pipeline_health !== undefined
    ) {
      const s = section.items[0];
      md += `| Metric | Value |\n|--------|-------|\n`;
      if (s.total_sources !== undefined)
        md += `| Total sources | ${s.total_sources} |\n`;
      if (s.active_sources !== undefined)
        md += `| Active sources | ${s.active_sources} |\n`;
      if (s.erroring_sources !== undefined)
        md += `| Erroring sources | ${s.erroring_sources} |\n`;
      if (s.pipeline_health !== undefined)
        md += `| Pipeline health | ${s.pipeline_health} |\n`;
      if (s.daily_spend_remaining !== undefined)
        md += `| Daily spend remaining | $${s.daily_spend_remaining} |\n`;
      if (s.source_type_counts && typeof s.source_type_counts === "object") {
        const counts = Object.entries(
          s.source_type_counts as Record<string, number>,
        )
          .map(([k, v]) => `${k}: ${v}`)
          .join(", ");
        md += `| Source types | ${counts} |\n`;
      }
      md += "\n";
      continue;
    }

    let idx = 1;
    for (const item of section.items) {
      const itemMd = formatItemMarkdown(item, idx);
      if (md.length + itemMd.length > budget - 200) {
        md += `\n... (${section.items.length - idx + 1} more results — refine query for specifics)\n`;
        break;
      }
      md += itemMd;
      idx++;
    }
    md += "\n";
  }

  if (itemIds.length > 0) {
    md += `---\nIDs: ${itemIds.join(", ")} (for feedback)\n`;
  }

  if (output._tip) {
    md += `\n> ${output._tip}\n`;
  }

  return md;
}
