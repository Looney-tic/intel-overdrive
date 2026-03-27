/**
 * Shared config module for overdrive-intel.
 *
 * Exports:
 *   VERSION    — read from package.json at module init
 *   getApiKey  — resolves OVERDRIVE_INTEL_API_KEY > OVERDRIVE_API_KEY > key file
 *   getApiUrl  — resolves OVERDRIVE_API_URL > api_url file > default
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

// Resolve __dirname for ESM modules
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// ---------------------------------------------------------------------------
// VERSION — read from package.json at module init
// Compiled output: dist/shared/config.js → ../../package.json = repo root
// ---------------------------------------------------------------------------

function readVersion(): string {
  try {
    const pkgPath = join(__dirname, "../../package.json");
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    return pkg.version ?? "0.0.0";
  } catch {
    return "0.0.0";
  }
}

export const VERSION: string = readVersion();

// ---------------------------------------------------------------------------
// API key resolution
// Order: OVERDRIVE_INTEL_API_KEY > OVERDRIVE_API_KEY (legacy) > key file
//
// NOTE: Do NOT export a module-level API_KEY constant. Callers MUST call
// getApiKey() to get a fresh value — prevents stale snapshots.
// ---------------------------------------------------------------------------

export function getApiKey(): string {
  // 1. Canonical env var
  if (process.env.OVERDRIVE_INTEL_API_KEY) {
    return process.env.OVERDRIVE_INTEL_API_KEY;
  }

  // 2. Legacy env var fallback (do not advertise)
  if (process.env.OVERDRIVE_API_KEY) {
    return process.env.OVERDRIVE_API_KEY;
  }

  // 3. Key file fallback
  try {
    const keyFile = join(homedir(), ".config", "overdrive-intel", "key");
    return readFileSync(keyFile, "utf-8").trim();
  } catch {
    // No key file
  }

  return "";
}

// ---------------------------------------------------------------------------
// API URL resolution
// Order: OVERDRIVE_API_URL env > api_url file > default
// Matches setup.ts (Plan 02) which writes ~/.config/overdrive-intel/api_url
// ---------------------------------------------------------------------------

const DEFAULT_API_URL = "https://inteloverdrive.com";

export function getApiUrl(): string {
  // 1. Env var
  if (process.env.OVERDRIVE_API_URL) {
    return process.env.OVERDRIVE_API_URL.replace(/\/+$/, "");
  }

  // 2. File fallback — written by `overdrive-intel setup`
  try {
    const urlFile = join(homedir(), ".config", "overdrive-intel", "api_url");
    const val = readFileSync(urlFile, "utf-8").trim();
    if (val) return val.replace(/\/+$/, "");
  } catch {
    // No file
  }

  // 3. Default
  return DEFAULT_API_URL;
}
