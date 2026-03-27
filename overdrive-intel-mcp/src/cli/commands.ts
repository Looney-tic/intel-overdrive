/**
 * CLI commands: search, feed, breaking
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

function formatItem(
  i: number,
  item: Record<string, unknown>,
  showSignificance = false,
): string {
  const title = (item.title as string) || "(no title)";
  const summary = truncate((item.summary as string) || "");
  const url = (item.url as string) || "";
  const significance = (item.significance as string) || "";

  const lines: string[] = [];
  lines.push(`${i}. ${title}`);
  if (showSignificance && significance) {
    lines.push(`   [${significance.toUpperCase()}]`);
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
// Commands
// ---------------------------------------------------------------------------

/**
 * Search for items matching a query.
 */
export async function runSearch(query: string): Promise<void> {
  requireApiKey();

  if (!query.trim()) {
    process.stderr.write("Usage: intel-overdrive search <query>\n");
    process.exit(1);
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
 */
export async function runFeed(options: {
  days?: number;
  type?: string;
}): Promise<void> {
  requireApiKey();

  const params: Record<string, string | number> = {
    limit: 15,
    sort: "significance",
  };
  if (options.days) params.days = options.days;
  if (options.type) params.feed_type = options.type;

  const data = (await apiGet("/v1/feed", params)) as Record<string, unknown>;
  const items = (data.items as Record<string, unknown>[]) || [];
  const days = options.days ?? 7;

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
export async function runBreaking(options: { days?: number }): Promise<void> {
  requireApiKey();

  const params: Record<string, string | number> = {
    significance: "breaking,major",
    limit: 15,
    sort: "significance",
  };
  if (options.days) params.days = options.days;

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
