/**
 * Shared HTTP helper functions for overdrive-intel CLI commands.
 *
 * Uses Node.js built-in `fetch` (available since Node 18) — no external deps.
 * Both apiGet and apiPost use getApiKey() and getApiUrl() from config.ts.
 */

import { getApiKey, getApiUrl } from "./config.js";

function headers(apiKey: string): Record<string, string> {
  return { "X-API-Key": apiKey, Accept: "application/json" };
}

/**
 * HTTP GET with query params. Uses the API key from config.
 * Returns parsed JSON or throws on non-ok response.
 */
export async function apiGet(
  path: string,
  params: Record<string, string | number> = {},
): Promise<unknown> {
  const apiKey = getApiKey();
  const url = new URL(`${getApiUrl()}${path}`);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") {
      url.searchParams.set(k, String(v));
    }
  }

  const response = await fetch(url.toString(), {
    method: "GET",
    headers: headers(apiKey),
    signal: AbortSignal.timeout(30_000),
  });

  if (!response.ok) {
    throw new Error(`API error ${response.status}: ${await response.text()}`);
  }

  return response.json();
}

/**
 * HTTP POST with JSON body. Uses the API key from config.
 * Returns parsed JSON or throws on non-ok response.
 */
export async function apiPost(
  path: string,
  body: Record<string, unknown>,
): Promise<unknown> {
  const apiKey = getApiKey();
  const url = `${getApiUrl()}${path}`;
  const response = await fetch(url, {
    method: "POST",
    headers: { ...headers(apiKey), "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    throw new Error(`API error ${response.status}: ${await response.text()}`);
  }
  return response.json();
}
