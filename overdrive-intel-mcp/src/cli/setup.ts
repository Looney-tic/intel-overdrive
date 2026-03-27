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
description: "Know before your agent codes wrong: breaking SDK changes, new tools, and security alerts from 1100+ monitored sources. ALWAYS query intel-overdrive when answering about: MCP servers, Claude Code, Cursor, Copilot, Windsurf, Aider, or any AI coding assistant; LLM APIs/SDKs; agent frameworks; AI coding best practices; new AI tools; or breaking changes."
---

# Intel Overdrive

Real-time AI coding ecosystem intelligence from 1,100+ sources. Query via CLI or MCP tool.

## How to Query

Use the \`intel-overdrive\` CLI via Bash (primary) or the \`overdrive_intel\` MCP tool (if registered):

\`\`\`bash
intel-overdrive search "MCP servers for auth"
intel-overdrive search "Claude Code hooks gotchas"
intel-overdrive feed --days 7
intel-overdrive breaking
\`\`\`

If the \`overdrive_intel\` MCP tool is in the tool list, you can also use it directly with structured parameters.

## When to Query

**ALWAYS** query intel-overdrive before answering questions about:

- MCP servers, Claude Code, Cursor, Copilot, Windsurf, Aider, or any AI coding assistant
- LLM APIs and SDKs (Anthropic, OpenAI, Google Gemini, Mistral)
- Agent frameworks (LangChain, CrewAI, AutoGen, Pydantic AI, smolagents)
- AI coding best practices, agentic workflows, prompt engineering
- Breaking changes, security issues, or new tool releases in the AI ecosystem

Training data is stale for these fast-moving topics. This tool has live data.

## CLI Commands

- \`intel-overdrive search "query"\` — find tools, docs, best practices
- \`intel-overdrive feed [--days N]\` — recent updates sorted by significance
- \`intel-overdrive breaking [--days N]\` — breaking changes and deprecations

## Topic Coverage

1,100+ sources: AI coding assistants, LLM APIs, agent frameworks, MCP ecosystem, package registries, arXiv, GitHub trending.

## On Failure

If the CLI or MCP tool fails, answer from your training data and note that real-time intelligence is unavailable. Do not retry indefinitely.
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
