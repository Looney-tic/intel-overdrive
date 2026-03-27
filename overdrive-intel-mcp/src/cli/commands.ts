/**
 * CLI commands: search, feed, breaking, briefing, library, similar, action-items, status
 *
 * Human-facing output to stdout. All commands check for an API key first.
 */

import { getApiKey } from "../shared/config.js";
import { apiGet } from "../shared/api-client.js";

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function truncate(text: string, maxLen = 200): string {
  if (!text) return "";
  return text.length <= maxLen ? text : text.slice(0, maxLen - 3) + "...";
}

function formatDate(item: Record<string, unknown>): string {
  const raw =
    (item.published_at as string) || (item.created_at as string) || "";
  if (!raw) return "";
  try {
    return new Date(raw).toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

function formatItem(
  i: number,
  item: Record<string, unknown>,
  showSignificance = false,
): string {
  const title = (item.title as string) || "(no title)";
  const summary = truncate(
    (item.summary as string) || (item.excerpt as string) || "",
  );
  const url = (item.url as string) || "";
  const significance = (item.significance as string) || "";
  const date = formatDate(item);

  const lines: string[] = [];
  lines.push(`${i}. ${title}`);

  // Significance + date on same line when both present
  if (showSignificance && significance && date) {
    lines.push(`   [${significance.toUpperCase()}] ${date}`);
  } else if (showSignificance && significance) {
    lines.push(`   [${significance.toUpperCase()}]`);
  } else if (date) {
    lines.push(`   ${date}`);
  }

  if (summary) {
    lines.push(`   ${summary}`);
  }
  if (url) {
    lines.push(`   ${url}`);
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Guard: check API key
// ---------------------------------------------------------------------------

function requireApiKey(): string {
  const key = getApiKey();
  if (!key) {
    process.stderr.write("No API key. Run: intel-overdrive setup\n");
    process.exit(1);
  }
  return key;
}

// ---------------------------------------------------------------------------
// Best-practice query detection (QUAL-03)
// ---------------------------------------------------------------------------

const BEST_PRACTICE_KEYWORDS = [
  "best practice",
  "best practices",
  "how to",
  "gotcha",
  "pattern",
  "guide",
  "tutorial",
  "recommend",
];

function isBestPracticeQuery(query: string): boolean {
  const lower = query.toLowerCase();
  return BEST_PRACTICE_KEYWORDS.some((kw) => lower.includes(kw));
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

/**
 * Search for items matching a query.
 * Routes to library endpoint for best-practice queries (QUAL-03).
 */
export async function runSearch(query: string): Promise<void> {
  requireApiKey();

  if (!query.trim()) {
    process.stderr.write("Usage: intel-overdrive search <query>\n");
    process.exit(1);
  }

  // QUAL-03: Route best-practice queries to library endpoint
  if (isBestPracticeQuery(query)) {
    const libData = (await apiGet("/v1/library/search", {
      q: query,
      limit: "10",
    })) as Record<string, unknown>;

    const libItems = (libData.items as Record<string, unknown>[]) || [];

    if (libItems.length > 0) {
      console.log(
        `\nSearch results for: "${query}" (routed to library for best-practice results)\n`,
      );
      for (let i = 0; i < libItems.length; i++) {
        const item = libItems[i];
        const entryType = (item.entry_type as string) || "guide";
        const title = (item.title as string) || "(no title)";
        const tldr = truncate((item.tldr as string) || "");
        const topicPath = (item.topic_path as string) || "";
        const confidence = (item.confidence as string) || "";
        const date = formatDate(item);

        const lines: string[] = [];
        lines.push(`${i + 1}. [${entryType}] ${title}`);
        if (date) lines.push(`   ${date}`);
        if (tldr) lines.push(`   ${tldr}`);
        const meta: string[] = [];
        if (topicPath) meta.push(`Topic: ${topicPath}`);
        if (confidence) meta.push(`Confidence: ${confidence}`);
        if (meta.length > 0) lines.push(`   ${meta.join(" | ")}`);
        console.log(lines.join("\n"));
        console.log();
      }
      return;
    }
    // Fall back to regular search if library returns 0 results
  }

  const data = (await apiGet("/v1/search", {
    q: query,
    limit: "10",
  })) as Record<string, unknown>;

  const items =
    (data.items as Record<string, unknown>[]) ||
    (data.results as Record<string, unknown>[]) ||
    [];

  if (items.length === 0) {
    console.log(`No results found for: "${query}"`);
    return;
  }

  console.log(`\nSearch results for: "${query}"\n`);
  for (let i = 0; i < items.length; i++) {
    console.log(formatItem(i + 1, items[i]));
    console.log();
  }
}

/**
 * Show recent feed items.
 * Uses sort=score for windows > 7 days to avoid ranking monotony (QUAL-01).
 */
export async function runFeed(options: {
  days?: number;
  type?: string;
  tag?: string;
}): Promise<void> {
  requireApiKey();

  const days = options.days ?? 7;

  const params: Record<string, string | number> = {
    limit: 15,
    // QUAL-01: Use score sort for longer windows to mix recency + significance
    sort: days > 7 ? "score" : "significance",
  };
  if (options.days) params.days = options.days;
  if (options.type) params.feed_type = options.type;
  if (options.tag) params.tag = options.tag;

  const data = (await apiGet("/v1/feed", params)) as Record<string, unknown>;
  const items = (data.items as Record<string, unknown>[]) || [];

  if (items.length === 0) {
    console.log(`No feed items found in the last ${days} days.`);
    return;
  }

  console.log(`\nRecent feed (last ${days} days)\n`);
  for (let i = 0; i < items.length; i++) {
    console.log(formatItem(i + 1, items[i], true));
    console.log();
  }
}

/**
 * Show breaking changes.
 */
export async function runBreaking(options: {
  days?: number;
  tag?: string;
}): Promise<void> {
  requireApiKey();

  const params: Record<string, string | number> = {
    significance: "breaking,major",
    limit: 15,
    sort: "significance",
  };
  if (options.days) params.days = options.days;
  if (options.tag) params.tag = options.tag;

  const data = (await apiGet("/v1/feed", params)) as Record<string, unknown>;
  const items = (data.items as Record<string, unknown>[]) || [];
  const days = options.days ?? 7;

  if (items.length === 0) {
    console.log(`No breaking changes found in the last ${days} days.`);
    return;
  }

  console.log(`\nBreaking changes (last ${days} days)\n`);
  for (let i = 0; i < items.length; i++) {
    console.log(formatItem(i + 1, items[i], true));
    console.log();
  }
}

/**
 * Get a synthesized intelligence briefing via /v1/context-pack.
 */
export async function runBriefing(options: {
  days?: number;
  topic?: string;
}): Promise<void> {
  requireApiKey();

  const days = options.days ?? 7;

  const params: Record<string, string | number> = {
    format: "json",
    budget: 2000,
    days,
  };
  if (options.topic) params.topic = options.topic;

  const data = (await apiGet("/v1/context-pack", params)) as Record<
    string,
    unknown
  >;
  const meta = (data.meta as Record<string, unknown>) || {};
  const items = (data.items as Record<string, unknown>[]) || [];
  const itemsIncluded = (meta.items_included as number) ?? items.length;

  const topicLabel = options.topic ? ` — ${options.topic}` : "";
  console.log(`\nIntelligence Briefing (last ${days} days${topicLabel})\n`);
  console.log(`${itemsIncluded} item(s) included\n`);

  for (let i = 0; i < items.length; i++) {
    console.log(formatItem(i + 1, items[i], true));
    console.log();
  }
}

/**
 * Search best practices and guides via /v1/library/search.
 */
export async function runLibrary(query: string): Promise<void> {
  requireApiKey();

  if (!query.trim()) {
    process.stderr.write("Usage: intel-overdrive library <query>\n");
    process.exit(1);
  }

  const data = (await apiGet("/v1/library/search", {
    q: query,
    limit: 10,
  })) as Record<string, unknown>;

  const items = (data.items as Record<string, unknown>[]) || [];

  if (items.length === 0) {
    console.log(`No library results found for: "${query}"`);
    return;
  }

  console.log(`\nLibrary results for: "${query}"\n`);
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const entryType = (item.entry_type as string) || "guide";
    const title = (item.title as string) || "(no title)";
    const tldr = truncate((item.tldr as string) || "");
    const topicPath = (item.topic_path as string) || "";
    const confidence = (item.confidence as string) || "";
    const date = formatDate(item);

    const lines: string[] = [];
    lines.push(`${i + 1}. [${entryType}] ${title}`);
    if (date) lines.push(`   ${date}`);
    if (tldr) lines.push(`   ${tldr}`);
    const meta: string[] = [];
    if (topicPath) meta.push(`Topic: ${topicPath}`);
    if (confidence) meta.push(`Confidence: ${confidence}`);
    if (meta.length > 0) lines.push(`   ${meta.join(" | ")}`);
    console.log(lines.join("\n"));
    console.log();
  }
}

/**
 * Find semantically similar items via /v1/similar.
 */
export async function runSimilar(concept: string): Promise<void> {
  requireApiKey();

  if (!concept.trim()) {
    process.stderr.write("Usage: intel-overdrive similar <concept>\n");
    process.exit(1);
  }

  const data = (await apiGet("/v1/similar", {
    concept,
    limit: 10,
  })) as Record<string, unknown>;

  const items = (data.items as Record<string, unknown>[]) || [];

  if (items.length === 0) {
    console.log(`No similar items found for: "${concept}"`);
    return;
  }

  console.log(`\nItems similar to: "${concept}"\n`);
  for (let i = 0; i < items.length; i++) {
    console.log(formatItem(i + 1, items[i], true));
    console.log();
  }
}

/**
 * Show items needing attention via /v1/action-items.
 */
export async function runActionItems(query?: string): Promise<void> {
  requireApiKey();

  const params: Record<string, string | number> = {};
  if (query && query.trim()) params.q = query;

  const data = (await apiGet("/v1/action-items", params)) as Record<
    string,
    unknown
  >;
  const items = (data.items as Record<string, unknown>[]) || [];

  if (items.length === 0) {
    console.log("No action items requiring attention.");
    return;
  }

  console.log(`\nAction items requiring attention\n`);
  for (let i = 0; i < items.length; i++) {
    console.log(formatItem(i + 1, items[i], true));
    console.log();
  }
}

/**
 * Show pipeline health and source status via /v1/status.
 */
export async function runStatus(): Promise<void> {
  requireApiKey();

  const data = (await apiGet("/v1/status", {})) as Record<string, unknown>;

  const pipelineHealth = (data.pipeline_health as string) || "unknown";
  const totalSources = (data.total_sources as number) ?? 0;
  const activeSources = (data.active_sources as number) ?? 0;
  const erroringSources = (data.erroring_sources as number) ?? 0;
  const sourceTypeCounts =
    (data.source_type_counts as Record<string, number>) || {};
  const dailySpendRemaining = (data.daily_spend_remaining as number) ?? null;

  console.log(`\nPipeline Status\n`);
  console.log(`Health:   ${pipelineHealth.toUpperCase()}`);
  console.log(
    `Sources:  ${activeSources} active / ${totalSources} total (${erroringSources} erroring)`,
  );

  if (Object.keys(sourceTypeCounts).length > 0) {
    console.log(`\nSource types:`);
    for (const [type, count] of Object.entries(sourceTypeCounts)) {
      console.log(`  ${type}: ${count}`);
    }
  }

  if (dailySpendRemaining !== null) {
    console.log(`\nDaily budget remaining: $${dailySpendRemaining.toFixed(4)}`);
  }

  console.log();
}
