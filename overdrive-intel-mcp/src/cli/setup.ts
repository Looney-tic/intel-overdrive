/**
 * Setup command: key provisioning, CLI install, SKILL.md install
 *
 * Ports setup.sh logic to Node.js for a clean cross-platform experience.
 * Uses only Node.js built-ins — no external deps.
 */

import {
  writeFileSync,
  chmodSync,
  readFileSync,
  mkdirSync,
  existsSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import * as https from "node:https";
import { getApiUrl } from "../shared/config.js";

// ---------------------------------------------------------------------------
// Bundled SKILL.md content
// ---------------------------------------------------------------------------

const SKILL_MD_CONTENT = `---
name: intel-overdrive
description: "Query live AI ecosystem intelligence when users ask about MCP servers, Claude Code features, Cursor, Copilot, AI coding assistants, LLM SDKs (Anthropic, OpenAI, Gemini), agent frameworks (LangChain, CrewAI), breaking changes, new AI tools, or 'what's new'. Use when the user asks 'what MCP servers exist for X', 'any breaking changes in Y', 'best practice for Z', or before recommending any AI tool or framework."
---

# Intel Overdrive

Live AI ecosystem intelligence from 1,100+ sources. Your training data is stale for these topics — this tool has current data.

## When to Use

Use intel-overdrive when the user:

- Asks about MCP servers, Claude Code, Cursor, Copilot, Windsurf, Aider, or any AI coding tool
- Asks about LLM SDKs or APIs (Anthropic, OpenAI, Gemini, Mistral)
- Asks about agent frameworks (LangChain, CrewAI, AutoGen, Pydantic AI)
- Asks "what's new", "what changed", "any updates" in AI/coding tools
- Asks about breaking changes, deprecations, or security issues in AI tools
- Needs a tool recommendation ("best MCP for X", "which framework for Y")
- Is about to use an AI SDK — check for breaking changes first

## How to Query

Check if CLI is installed (once per session): \`which intel-overdrive\`

If found, query directly:

\`\`\`bash
intel-overdrive search "MCP servers for auth"
intel-overdrive feed --days 7
intel-overdrive breaking
\`\`\`

| User asks... | Command |
|---|---|
| "What MCP servers exist for X?" | \`intel-overdrive search "MCP X"\` |
| "What's new this week?" | \`intel-overdrive feed --days 7\` |
| "Any breaking changes?" | \`intel-overdrive breaking\` |

## On Failure

If the CLI fails, answer from training data and note real-time data is unavailable.
`;


// ---------------------------------------------------------------------------
// Low-level HTTPS helper (used before we have a key, so can't use apiGet)
// ---------------------------------------------------------------------------

interface HttpResult {
  statusCode: number;
  body: string;
}

function httpsRequest(
  options: https.RequestOptions,
  body?: string,
): Promise<HttpResult> {
  return new Promise((resolve, reject) => {
    const req = https.request(options, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("end", () => {
        resolve({
          statusCode: res.statusCode ?? 0,
          body: Buffer.concat(chunks).toString("utf-8"),
        });
      });
    });
    req.on("error", reject);
    req.setTimeout(15_000, () => {
      req.destroy(new Error("Request timed out"));
    });
    if (body) req.write(body);
    req.end();
  });
}

function parseUrl(urlStr: string): URL {
  return new URL(urlStr);
}

// ---------------------------------------------------------------------------
// Main setup function
// ---------------------------------------------------------------------------

export async function runSetup(): Promise<void> {
  const configDir = join(homedir(), ".config", "overdrive-intel");
  const keyFile = join(configDir, "key");
  const urlFile = join(configDir, "api_url");
  const apiUrl = getApiUrl();

  console.log("");
  console.log("  intel-overdrive setup");
  console.log("");

  // Step a: Check for existing key
  let apiKey = "";
  if (existsSync(keyFile)) {
    try {
      apiKey = readFileSync(keyFile, "utf-8").trim();
      if (apiKey) {
        console.log(`  Found existing key: ${apiKey.slice(0, 14)}...`);
      }
    } catch {
      // Key file unreadable — re-register
    }
  }

  // Step b: Register if no key
  if (!apiKey) {
    console.log("  Registering new account...");

    const parsed = parseUrl(`${apiUrl}/v1/auth/register`);
    const requestBody = "{}";
    const result = await httpsRequest(
      {
        hostname: parsed.hostname,
        port: parsed.port ? parseInt(parsed.port) : 443,
        path: parsed.pathname,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(requestBody),
          Accept: "application/json",
        },
      },
      requestBody,
    );

    if (result.statusCode === 201) {
      try {
        const data = JSON.parse(result.body) as Record<string, unknown>;
        apiKey = (data.api_key as string) || "";
      } catch {
        console.error("  Error: Failed to parse registration response.");
        process.exit(1);
      }
    } else if (result.statusCode === 409) {
      // Already registered — try existing key file
      if (existsSync(keyFile)) {
        apiKey = readFileSync(keyFile, "utf-8").trim();
        console.log(`  Found existing key: ${apiKey.slice(0, 14)}...`);
      } else {
        console.error("  Already registered. Import your key:");
        console.error(`    intel-overdrive setup --key YOUR_KEY`);
        process.exit(1);
      }
    } else {
      let errMsg = `HTTP ${result.statusCode}`;
      try {
        const data = JSON.parse(result.body) as Record<string, unknown>;
        errMsg = (data.detail as string) || (data.error as string) || errMsg;
      } catch {
        // Ignore parse error
      }
      console.error(`  Error: ${errMsg}`);
      process.exit(1);
    }

    if (!apiKey) {
      console.error("  Error: No API key received.");
      process.exit(1);
    }
  }

  // Step c+d: Write key and API URL files
  mkdirSync(configDir, { recursive: true });
  writeFileSync(keyFile, apiKey, { mode: 0o600 });
  chmodSync(keyFile, 0o600); // Always chmodSync after writeFileSync (project gotcha)
  writeFileSync(urlFile, apiUrl);

  // Step e: Verify key works
  console.log("  Verifying key...");
  let itemCount = "";
  try {
    const parsed = parseUrl(`${apiUrl}/v1/feed?limit=1`);
    const verifyResult = await httpsRequest({
      hostname: parsed.hostname,
      port: parsed.port ? parseInt(parsed.port) : 443,
      path: `${parsed.pathname}${parsed.search}`,
      method: "GET",
      headers: {
        "X-API-Key": apiKey,
        Accept: "application/json",
      },
    });
    if (verifyResult.statusCode === 200) {
      const data = JSON.parse(verifyResult.body) as Record<string, unknown>;
      itemCount = String(data.total ?? "");
    }
  } catch {
    // Non-fatal — key may still be valid
  }

  // Step f: Ensure global install (handles npx case)
  let globalOk = false;
  try {
    execFileSync("which", ["intel-overdrive"], { stdio: "ignore" });
    globalOk = true; // Already globally installed
  } catch {
    // Not globally installed — install now
    console.log("  Installing intel-overdrive globally...");
    try {
      execFileSync("npm", ["install", "-g", "intel-overdrive@latest"], {
        stdio: "inherit",
      });
      globalOk = true;
    } catch {
      console.error(
        "  Warning: Could not install globally. Use npx intel-overdrive <command> instead.",
      );
    }
  }

  // Step g: Install SKILL.md
  const skillDir = join(homedir(), ".claude", "skills", "intel-overdrive");
  // Also clean up old directory name if it exists
  const oldSkillDir = join(homedir(), ".claude", "skills", "overdrive-intel");
  try {
    const { rmSync } = await import("node:fs");
    if (existsSync(oldSkillDir)) rmSync(oldSkillDir, { recursive: true });
  } catch {
    /* ignore */
  }
  const skillFile = join(skillDir, "SKILL.md");
  try {
    mkdirSync(skillDir, { recursive: true });
    writeFileSync(skillFile, SKILL_MD_CONTENT, { mode: 0o644 });
    console.log("  Installed SKILL.md to ~/.claude/skills/intel-overdrive/");
  } catch (err) {
    console.error(`  Warning: Could not install SKILL.md: ${err}`);
  }

  // Step h: Print success summary
  console.log("");
  console.log(`  ✓ Ready. API key: ${apiKey.slice(0, 20)}...`);
  if (itemCount) {
    console.log(`  ✓ Verified: ${itemCount} items tracked`);
  }
  if (globalOk) {
    console.log("  ✓ CLI installed globally");
  }
  console.log("  ✓ SKILL.md installed");
  console.log("");
  console.log('  Your agent can now use: intel-overdrive search "query"');
  console.log('  Try it:  intel-overdrive search "MCP servers for auth"');
  console.log("");
  console.log("  Optional: register as MCP server for structured tool access:");
  console.log("    intel-overdrive mcp-enable");
  console.log("");
}
