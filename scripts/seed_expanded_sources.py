"""Comprehensive source expansion — all discovered AI ecosystem feeds.

Usage: python scripts/seed_expanded_sources.py

Adds ~90 new sources across all existing adapter types:
- RSS/Atom: status pages, GitHub releases, PyPI RSS, Substacks, subreddits, SO tags
- Scraper: changelogs, blogs, docs pages without RSS
- github-deep: new repos to watch
- Bluesky: additional keyword searches and account feeds

Idempotent: checks if source.id exists before inserting, skips if present.
"""

import asyncio
import sys

sys.path.insert(0, ".")  # Allow running from project root

import src.core.init_db as _db
from src.core.init_db import init_db, close_db
from src.models.models import Source
from sqlalchemy import select

# ===========================================================================
# RSS/Atom feeds — status pages, GitHub releases, PyPI RSS, newsletters, etc.
# ===========================================================================

RSS_SOURCES = [
    # --- Status pages (RSS/Atom) ---
    {
        "id": "rss:claude-status",
        "name": "Claude Status Incidents",
        "url": "https://status.claude.com/history.atom",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:openai-status",
        "name": "OpenAI Status Incidents",
        "url": "https://status.openai.com/history.atom",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:github-status",
        "name": "GitHub Status Incidents",
        "url": "https://www.githubstatus.com/history.rss",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:cursor-status",
        "name": "Cursor Status Incidents",
        "url": "https://status.cursor.com/history.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:cohere-status",
        "name": "Cohere Status Incidents",
        "url": "https://status.cohere.com/history.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:xai-status",
        "name": "xAI Status",
        "url": "https://status.x.ai/feed.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- GitHub releases: LLM providers SDKs ---
    {
        "id": "rss:gh-openai-node",
        "name": "openai-node Releases",
        "url": "https://github.com/openai/openai-node/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-mistral-python",
        "name": "mistral-python Releases",
        "url": "https://github.com/mistralai/client-python/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-cohere-python",
        "name": "cohere-python Releases",
        "url": "https://github.com/cohere-ai/cohere-python/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-xai-sdk-python",
        "name": "xai-sdk-python Releases",
        "url": "https://github.com/xai-org/xai-sdk-python/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Voyage AI (embedding provider) ---
    {
        "id": "rss:voyage-ai-blog",
        "name": "Voyage AI Blog",
        "url": "https://blog.voyageai.com/feed/",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-voyage-python",
        "name": "voyageai-python Releases",
        "url": "https://github.com/voyage-ai/voyageai-python/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Framework breaking changes (AI agents generate outdated code without these) ---
    {
        "id": "rss:gh-nextjs",
        "name": "Next.js Releases",
        "url": "https://github.com/vercel/next.js/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-drizzle-orm",
        "name": "Drizzle ORM Releases",
        "url": "https://github.com/drizzle-team/drizzle-orm/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-sqlalchemy",
        "name": "SQLAlchemy Releases",
        "url": "https://github.com/sqlalchemy/sqlalchemy/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-prisma",
        "name": "Prisma Releases",
        "url": "https://github.com/prisma/prisma/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:typescript-devblog",
        "name": "TypeScript DevBlog",
        "url": "https://devblogs.microsoft.com/typescript/feed/",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:tailwindcss-blog",
        "name": "Tailwind CSS Blog",
        "url": "https://tailwindcss.com/feeds/feed.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:python-insider",
        "name": "Python Insider Blog",
        "url": "https://blog.python.org/feeds/posts/default?alt=rss",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:nodejs-blog",
        "name": "Node.js Blog",
        "url": "https://nodejs.org/en/feed/blog.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:docker-blog",
        "name": "Docker Blog",
        "url": "https://www.docker.com/blog/feed/",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:react-blog",
        "name": "React Official Blog",
        "url": "https://react.dev/rss.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-fastapi",
        "name": "FastAPI PyPI Releases",
        "url": "https://pypi.org/rss/project/fastapi/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- AI-assisted development practitioners ---
    {
        "id": "rss:hamel-husain",
        "name": "Hamel Husain Blog",
        "url": "https://hamel.dev/index.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:harper-reed",
        "name": "Harper Reed Blog",
        "url": "https://harper.blog/index.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:sei-cmu",
        "name": "SEI CMU Blog (Software Engineering Institute)",
        "url": "https://insights.sei.cmu.edu/atom.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:daniel-miessler",
        "name": "Unsupervised Learning (Daniel Miessler)",
        "url": "https://newsletter.danielmiessler.com/feed",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:sourcegraph-blog",
        "name": "Sourcegraph Engineering Blog",
        "url": "https://sourcegraph.com/blog.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:cohere-engineering",
        "name": "Cohere Engineering Blog",
        "url": "https://txt.cohere.ai/rss/",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:aws-opensource",
        "name": "AWS Open Source Blog",
        "url": "https://aws.amazon.com/blogs/opensource/feed/",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Community conventions and agent workflows ---
    {
        "id": "rss:reddit-claudecode",
        "name": "r/ClaudeCode",
        "url": "https://www.reddit.com/r/ClaudeCode.rss",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:reddit-claudeai",
        "name": "r/ClaudeAI",
        "url": "https://www.reddit.com/r/ClaudeAI.rss",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:awesome-cursorrules-commits",
        "name": "Awesome CursorRules Commits",
        "url": "https://github.com/PatrickJS/awesome-cursorrules/commits.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:claude-md-templates-commits",
        "name": "Claude MD Templates Commits",
        "url": "https://github.com/abhishekray07/claude-md-templates/commits.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Batch 2+3: Practitioner blogs (highest signal AI-assisted dev workflows) ---
    {
        "id": "rss:martin-fowler",
        "name": "Martin Fowler Blog",
        "url": "https://martinfowler.com/feed.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:addy-osmani",
        "name": "Addy Osmani — Elevate",
        "url": "https://addyo.substack.com/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:builder-io-blog",
        "name": "Builder.io Blog (Steve Sewell)",
        "url": "https://www.builder.io/blog/rss.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:aider-blog",
        "name": "Aider Blog",
        "url": "https://aider.chat/feed.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:fast-ai-blog",
        "name": "fast.ai Blog",
        "url": "https://www.fast.ai/index.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:jason-liu-jxnl",
        "name": "Jason Liu (jxnl) — RAG Engineering",
        "url": "https://jxnl.co/feed_rss_created.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:jkatz-pgvector",
        "name": "Jonathan Katz Blog (pgvector)",
        "url": "https://jkatz05.com/post/index.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:crunchy-data-blog",
        "name": "Crunchy Data Blog (pgvector production)",
        "url": "https://www.crunchydata.com/blog/rss.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:planetscale-blog",
        "name": "PlanetScale Engineering Blog",
        "url": "https://planetscale.com/blog/feed.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Batch 2+3: Additional framework release feeds ---
    {
        "id": "rss:nextjs-blog",
        "name": "Next.js Blog",
        "url": "https://nextjs.org/feed.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:prisma-blog",
        "name": "Prisma Blog",
        "url": "https://www.prisma.io/blog/rss.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-react",
        "name": "React GitHub Releases",
        "url": "https://github.com/facebook/react/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-typescript",
        "name": "TypeScript GitHub Releases",
        "url": "https://github.com/microsoft/TypeScript/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-vercel-ai-sdk",
        "name": "Vercel AI SDK Releases",
        "url": "https://github.com/vercel/ai/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-docker-compose",
        "name": "Docker Compose Releases",
        "url": "https://github.com/docker/compose/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-nodejs",
        "name": "Node.js Runtime Releases",
        "url": "https://github.com/nodejs/node/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-tailwindcss",
        "name": "Tailwind CSS Releases",
        "url": "https://github.com/tailwindlabs/tailwindcss/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-ms-agent-framework",
        "name": "Microsoft Agent Framework Releases",
        "url": "https://github.com/microsoft/agent-framework/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Batch 2+3: Security and antipattern research ---
    {
        "id": "rss:semgrep-blog",
        "name": "Semgrep Blog (AppSec + AI Agent Testing)",
        "url": "https://semgrep.dev/blog/rss",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:coderabbit-blog",
        "name": "CodeRabbit Blog (AI Code Quality)",
        "url": "https://www.coderabbit.ai/feed",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Batch 3: Additional community subreddits ---
    {
        "id": "rss:reddit-vibecoding",
        "name": "r/vibecoding",
        "url": "https://www.reddit.com/r/vibecoding/.rss",
        "tier": "tier2",
        "poll": 1800,
    },
    {
        "id": "rss:gh-claude-code",
        "name": "claude-code Releases",
        "url": "https://github.com/anthropics/claude-code/releases.atom",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:gh-openai-python",
        "name": "openai-python Releases",
        "url": "https://github.com/openai/openai-python/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- GitHub releases: AI frameworks ---
    {
        "id": "rss:gh-langchain",
        "name": "LangChain Releases",
        "url": "https://github.com/langchain-ai/langchain/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-llamaindex",
        "name": "LlamaIndex Releases",
        "url": "https://github.com/run-llama/llama_index/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-crewai",
        "name": "CrewAI Releases",
        "url": "https://github.com/crewAIInc/crewAI/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-autogen",
        "name": "AutoGen Releases",
        "url": "https://github.com/microsoft/autogen/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-semantic-kernel",
        "name": "Semantic Kernel Releases",
        "url": "https://github.com/microsoft/semantic-kernel/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-pydantic-ai",
        "name": "PydanticAI Releases",
        "url": "https://github.com/pydantic/pydantic-ai/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-smolagents",
        "name": "smolagents Releases",
        "url": "https://github.com/huggingface/smolagents/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-instructor",
        "name": "Instructor Releases",
        "url": "https://github.com/instructor-ai/instructor/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-outlines",
        "name": "Outlines Releases",
        "url": "https://github.com/outlines-dev/outlines/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-dspy",
        "name": "DSPy Releases",
        "url": "https://github.com/stanfordnlp/dspy/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-haystack",
        "name": "Haystack Releases",
        "url": "https://github.com/deepset-ai/haystack/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- GitHub releases: AI coding tools ---
    {
        "id": "rss:gh-continue",
        "name": "Continue Releases",
        "url": "https://github.com/continuedev/continue/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-cline",
        "name": "Cline Releases",
        "url": "https://github.com/cline/cline/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-roo-code",
        "name": "Roo Code Releases",
        "url": "https://github.com/RooCodeInc/Roo-Code/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-aider",
        "name": "Aider Releases",
        "url": "https://github.com/Aider-AI/aider/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-copilot-cli",
        "name": "Copilot CLI Releases",
        "url": "https://github.com/github/copilot-cli/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:gh-tabnine",
        "name": "Tabnine Releases",
        "url": "https://github.com/codota/TabNine/releases.atom",
        "tier": "tier3",
        "poll": 86400,
    },
    # --- GitHub releases: MCP ecosystem ---
    {
        "id": "rss:gh-mcp-spec",
        "name": "MCP Spec Releases",
        "url": "https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-mcp-spec-commits",
        "name": "MCP Spec Commits",
        "url": "https://github.com/modelcontextprotocol/modelcontextprotocol/commits/main.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-mcp-registry",
        "name": "MCP Registry Repo Releases",
        "url": "https://github.com/modelcontextprotocol/registry/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-mcp-servers-releases",
        "name": "MCP Servers Releases",
        "url": "https://github.com/modelcontextprotocol/servers/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-pulsemcp-servers",
        "name": "PulseMCP Servers Releases",
        "url": "https://github.com/pulsemcp/mcp-servers/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- GitHub releases: Meta/open models ---
    {
        "id": "rss:gh-llama-models",
        "name": "Llama Models Releases",
        "url": "https://github.com/meta-llama/llama-models/releases.atom",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:gh-llama-commits",
        "name": "Llama Models Commits",
        "url": "https://github.com/meta-llama/llama-models/commits/main.atom",
        "tier": "tier3",
        "poll": 86400,
    },
    # --- Google Cloud / Vertex AI ---
    {
        "id": "rss:gcloud-status",
        "name": "Google Cloud Service Health",
        "url": "https://status.cloud.google.com/en/feed.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:vertex-ai-notes",
        "name": "Vertex AI Release Notes",
        "url": "https://docs.cloud.google.com/feeds/vertex-ai-product-group-release-notes.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gemini-cloud-notes",
        "name": "Gemini for Cloud Release Notes",
        "url": "https://docs.cloud.google.com/feeds/gemini-release-notes.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gemini-code-assist",
        "name": "Gemini Code Assist Release Notes",
        "url": "https://developers.google.com/feeds/gemini-code-assist-free-release-notes.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- PyPI RSS release feeds (new discovery! standard RSS, no adapter needed) ---
    {
        "id": "rss:pypi-anthropic",
        "name": "PyPI anthropic Releases",
        "url": "https://pypi.org/rss/project/anthropic/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-openai",
        "name": "PyPI openai Releases",
        "url": "https://pypi.org/rss/project/openai/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-google-genai",
        "name": "PyPI google-genai Releases",
        "url": "https://pypi.org/rss/project/google-genai/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-mistralai",
        "name": "PyPI mistralai Releases",
        "url": "https://pypi.org/rss/project/mistralai/releases.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-cohere",
        "name": "PyPI cohere Releases",
        "url": "https://pypi.org/rss/project/cohere/releases.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-pydantic-ai",
        "name": "PyPI pydantic-ai Releases",
        "url": "https://pypi.org/rss/project/pydantic-ai/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-langchain",
        "name": "PyPI langchain Releases",
        "url": "https://pypi.org/rss/project/langchain/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-llama-index",
        "name": "PyPI llama-index Releases",
        "url": "https://pypi.org/rss/project/llama-index/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-crewai",
        "name": "PyPI crewai Releases",
        "url": "https://pypi.org/rss/project/crewai/releases.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-smolagents",
        "name": "PyPI smolagents Releases",
        "url": "https://pypi.org/rss/project/smolagents/releases.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-instructor",
        "name": "PyPI instructor Releases",
        "url": "https://pypi.org/rss/project/instructor/releases.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-outlines",
        "name": "PyPI outlines Releases",
        "url": "https://pypi.org/rss/project/outlines/releases.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-dspy",
        "name": "PyPI dspy Releases",
        "url": "https://pypi.org/rss/project/dspy/releases.xml",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:pypi-haystack",
        "name": "PyPI farm-haystack Releases",
        "url": "https://pypi.org/rss/project/farm-haystack/releases.xml",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:pypi-semantic-kernel",
        "name": "PyPI semantic-kernel Releases",
        "url": "https://pypi.org/rss/project/semantic-kernel/releases.xml",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- Substack / Newsletter RSS ---
    {
        "id": "rss:bensbites",
        "name": "Ben's Bites Newsletter",
        "url": "https://bensbites.substack.com/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:latentspace",
        "name": "Latent Space Newsletter",
        "url": "https://latent.space/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:importai",
        "name": "Import AI Newsletter",
        "url": "https://importai.substack.com/feed",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:thesequence",
        "name": "The Sequence Newsletter",
        "url": "https://thesequence.substack.com/feed",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:pragmatic-eng",
        "name": "Pragmatic Engineer Newsletter",
        "url": "https://newsletter.pragmaticengineer.com/feed",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Subreddit feeds ---
    {
        "id": "rss:reddit-localllama",
        "name": "r/LocalLLaMA",
        "url": "https://www.reddit.com/r/LocalLLaMA.rss",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:reddit-chatgpt",
        "name": "r/ChatGPT",
        "url": "https://www.reddit.com/r/ChatGPT.rss",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:reddit-machinelearning",
        "name": "r/MachineLearning",
        "url": "https://www.reddit.com/r/MachineLearning.rss",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Dev.to additional tags ---
    {
        "id": "rss:devto-ai",
        "name": "dev.to AI Tag",
        "url": "https://dev.to/feed/tag/ai",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:devto-llm",
        "name": "dev.to LLM Tag",
        "url": "https://dev.to/feed/tag/llm",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:devto-vscode",
        "name": "dev.to VS Code Tag",
        "url": "https://dev.to/feed/tag/vscode",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Stack Overflow tag feeds ---
    {
        "id": "rss:so-language-model",
        "name": "SO language-model Tag",
        "url": "https://stackoverflow.com/feeds/tag?tagnames=language-model&sort=newest",
        "tier": "tier3",
        "poll": 3600,
    },
    {
        "id": "rss:so-openai-api",
        "name": "SO openai-api Tag",
        "url": "https://stackoverflow.com/feeds/tag?tagnames=openai-api&sort=newest",
        "tier": "tier3",
        "poll": 3600,
    },
    {
        "id": "rss:so-anthropic",
        "name": "SO anthropic Tag",
        "url": "https://stackoverflow.com/feeds/tag?tagnames=anthropic&sort=newest",
        "tier": "tier3",
        "poll": 86400,
    },
    # --- Blogs/news with RSS ---
    {
        "id": "rss:openai-news",
        "name": "OpenAI News RSS",
        "url": "https://openai.com/news/rss.xml",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:github-blog",
        "name": "GitHub Blog",
        "url": "https://github.blog/feed/",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:github-changelog",
        "name": "GitHub Changelog",
        "url": "https://github.blog/changelog/feed/",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:aws-q-developer",
        "name": "AWS Amazon Q Developer",
        "url": "https://aws.amazon.com/blogs/aws/category/amazon-q/amazon-q-developer/feed/",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:cursor-blog-atom",
        "name": "Cursor Blog Atom",
        "url": "https://cursor.sh/atom.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- HuggingFace papers (community RSS mirror) ---
    {
        "id": "rss:hf-papers",
        "name": "HuggingFace Daily Papers RSS",
        "url": "https://papers.takara.ai/api/feed",
        "tier": "tier1",
        "poll": 86400,
    },
    # --- Anthropic additional ---
    {
        "id": "rss:anthropic-status",
        "name": "Anthropic Status",
        "url": "https://status.anthropic.com/history.atom",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:gh-claude-code-action",
        "name": "claude-code-action Releases",
        "url": "https://github.com/anthropics/claude-code-action/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-claude-agent-sdk",
        "name": "claude-agent-sdk-python Releases",
        "url": "https://github.com/anthropics/claude-agent-sdk-python/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-anthropic-sdk-python",
        "name": "anthropic-sdk-python Releases",
        "url": "https://github.com/anthropics/anthropic-sdk-python/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:anthropic-news-3p",
        "name": "Anthropic News (3rd party RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:anthropic-eng-3p",
        "name": "Anthropic Engineering Blog (3rd party RSS)",
        "url": "https://raw.githubusercontent.com/conoro/anthropic-engineering-rss-feed/main/anthropic_engineering_rss.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:claude-code-changelog-3p",
        "name": "Claude Code Changelog (3rd party RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_changelog_claude_code.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- MCP SDKs ---
    {
        "id": "rss:gh-mcp-python-sdk",
        "name": "MCP Python SDK Releases",
        "url": "https://github.com/modelcontextprotocol/python-sdk/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-mcp-ts-sdk",
        "name": "MCP TypeScript SDK Releases",
        "url": "https://github.com/modelcontextprotocol/typescript-sdk/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-mcp-csharp-sdk",
        "name": "MCP C# SDK Releases",
        "url": "https://github.com/modelcontextprotocol/csharp-sdk/releases.atom",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:gh-mcp-kotlin-sdk",
        "name": "MCP Kotlin SDK Releases",
        "url": "https://github.com/modelcontextprotocol/kotlin-sdk/releases.atom",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:gh-mcp-go-sdk",
        "name": "MCP Go SDK Releases",
        "url": "https://github.com/modelcontextprotocol/go-sdk/releases.atom",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- OpenAI additional ---
    {
        "id": "rss:gh-openai-codex",
        "name": "OpenAI Codex CLI Releases",
        "url": "https://github.com/openai/codex/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Google additional ---
    {
        "id": "rss:google-dev-blog",
        "name": "Google Developers Blog",
        "url": "https://developers.googleblog.com/rss/",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-gemini-cli",
        "name": "Gemini CLI Releases",
        "url": "https://github.com/google-gemini/gemini-cli/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Mistral additional ---
    {
        "id": "rss:gh-mistral-vibe",
        "name": "Mistral Vibe CLI Releases",
        "url": "https://github.com/mistralai/mistral-vibe/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Meta additional ---
    {
        "id": "rss:gh-llama-stack",
        "name": "Llama Stack Releases",
        "url": "https://github.com/meta-llama/llama-stack/releases.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-llama-stack",
        "name": "PyPI llama-stack Releases",
        "url": "https://pypi.org/rss/project/llama-stack/releases.xml",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- xAI additional ---
    {
        "id": "rss:xai-news-3p",
        "name": "xAI News (3rd party RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_xainews.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-xai-sdk",
        "name": "PyPI xai-sdk Releases",
        "url": "https://pypi.org/rss/project/xai-sdk/releases.xml",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- AI coding tools: Cursor 3rd party ---
    {
        "id": "rss:cursor-changelog-3p",
        "name": "Cursor Changelog (3rd party)",
        "url": "https://cursor-changelog.com/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:cursor-community-3p",
        "name": "Cursor Blog (community RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_cursor.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Windsurf 3rd party ---
    {
        "id": "rss:windsurf-blog-3p",
        "name": "Windsurf Blog (community RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_windsurf_blog.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:windsurf-changelog-3p",
        "name": "Windsurf Changelog (community RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_windsurf_changelog.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Copilot ---
    {
        "id": "rss:gh-copilot-changelog",
        "name": "GitHub Copilot Changelog",
        "url": "https://github.blog/changelog/label/copilot/",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Framework additional releases ---
    {
        "id": "rss:gh-langgraph",
        "name": "LangGraph Releases",
        "url": "https://github.com/langchain-ai/langgraph/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-mastra",
        "name": "Mastra Releases",
        "url": "https://github.com/mastra-ai/mastra/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:gh-bolt-new",
        "name": "Bolt.new Releases",
        "url": "https://github.com/stackblitz/bolt.new/releases.atom",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- PyPI additional packages ---
    {
        "id": "rss:pypi-langchain-core",
        "name": "PyPI langchain-core Releases",
        "url": "https://pypi.org/rss/project/langchain-core/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-langgraph",
        "name": "PyPI langgraph Releases",
        "url": "https://pypi.org/rss/project/langgraph/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-llama-index-core",
        "name": "PyPI llama-index-core Releases",
        "url": "https://pypi.org/rss/project/llama-index-core/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-autogen-agentchat",
        "name": "PyPI autogen-agentchat Releases",
        "url": "https://pypi.org/rss/project/autogen-agentchat/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-autogen-core",
        "name": "PyPI autogen-core Releases",
        "url": "https://pypi.org/rss/project/autogen-core/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-mcp",
        "name": "PyPI mcp Releases",
        "url": "https://pypi.org/rss/project/mcp/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-aider-chat",
        "name": "PyPI aider-chat Releases",
        "url": "https://pypi.org/rss/project/aider-chat/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-haystack-ai",
        "name": "PyPI haystack-ai Releases",
        "url": "https://pypi.org/rss/project/haystack-ai/releases.xml",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- Awesome lists (commits.atom) ---
    {
        "id": "rss:awesome-mcp-punkpeye",
        "name": "awesome-mcp-servers (punkpeye) Commits",
        "url": "https://github.com/punkpeye/awesome-mcp-servers/commits.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:awesome-mcp-clients",
        "name": "awesome-mcp-clients Commits",
        "url": "https://github.com/punkpeye/awesome-mcp-clients/commits.atom",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:awesome-llm-apps",
        "name": "awesome-llm-apps Commits",
        "url": "https://github.com/Shubhamsaboo/awesome-llm-apps/commits.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:awesome-llm",
        "name": "Awesome-LLM Commits",
        "url": "https://github.com/Hannibal046/Awesome-LLM/commits.atom",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- HN keyword feeds via hnrss.org ---
    {
        "id": "rss:hn-100pts",
        "name": "HN Front Page 100+ Points",
        "url": "https://hnrss.org/frontpage?points=100",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:hn-show-50pts",
        "name": "HN Show HN 50+ Points",
        "url": "https://hnrss.org/show?points=50",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:hn-ai-coding",
        "name": "HN AI Coding Keyword",
        "url": "https://hnrss.org/newest?q=AI+coding",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:hn-llm",
        "name": "HN LLM Keyword",
        "url": "https://hnrss.org/newest?q=LLM",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:hn-claude",
        "name": "HN Claude Keyword",
        "url": "https://hnrss.org/newest?q=Claude",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:hn-mcp",
        "name": "HN MCP Keyword",
        "url": "https://hnrss.org/newest?q=MCP+OR+%22model+context+protocol%22",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:hn-vibe-coding",
        "name": "HN Vibe Coding Keyword",
        "url": "https://hnrss.org/newest?q=vibe+coding",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Reddit additional subreddits ---
    {
        "id": "rss:reddit-chatgptcoding",
        "name": "r/ChatGPTCoding",
        "url": "https://www.reddit.com/r/ChatGPTCoding/new/.rss",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:reddit-cursorai",
        "name": "r/CursorAI",
        "url": "https://www.reddit.com/r/CursorAI/new/.rss",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:reddit-langchain",
        "name": "r/LangChain",
        "url": "https://www.reddit.com/r/LangChain/new/.rss",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:reddit-openai",
        "name": "r/OpenAI",
        "url": "https://www.reddit.com/r/OpenAI/new/.rss",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:reddit-prompteng",
        "name": "r/PromptEngineering",
        "url": "https://www.reddit.com/r/PromptEngineering/new/.rss",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:reddit-copilot",
        "name": "r/Copilot",
        "url": "https://www.reddit.com/r/Copilot/new/.rss",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Dev.to additional ---
    {
        "id": "rss:devto-langchain",
        "name": "dev.to LangChain Tag",
        "url": "https://dev.to/feed/tag/langchain",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:devto-openai",
        "name": "dev.to OpenAI Tag",
        "url": "https://dev.to/feed/tag/openai",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Stack Overflow additional ---
    {
        "id": "rss:so-llm",
        "name": "SO large-language-model Tag",
        "url": "https://stackoverflow.com/feeds/tag/large-language-model",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:so-langchain",
        "name": "SO langchain Tag",
        "url": "https://stackoverflow.com/feeds/tag/langchain",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:so-copilot",
        "name": "SO github-copilot Tag",
        "url": "https://stackoverflow.com/feeds/tag/github-copilot",
        "tier": "tier3",
        "poll": 86400,
    },
    # --- Newsletters / Substacks additional ---
    {
        "id": "rss:ahead-of-ai",
        "name": "Ahead of AI (Sebastian Raschka)",
        "url": "https://magazine.sebastianraschka.com/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:last-week-in-ai",
        "name": "Last Week in AI",
        "url": "https://lastweekin.ai/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:interconnects",
        "name": "Interconnects (Nathan Lambert)",
        "url": "https://www.interconnects.ai/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:ai-snake-oil",
        "name": "AI Snake Oil",
        "url": "https://aisnakeoil.substack.com/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:rundown-ai",
        "name": "The Rundown AI",
        "url": "https://rss.beehiiv.com/feeds/2R3C6Bt5wj.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Independent blogs ---
    {
        "id": "rss:simonwillison",
        "name": "Simon Willison's Weblog",
        "url": "https://simonwillison.net/atom/everything/",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:the-gradient",
        "name": "The Gradient",
        "url": "https://thegradient.pub/rss/",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:chip-huyen",
        "name": "Chip Huyen Blog",
        "url": "https://huyenchip.com/feed",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- AI safety / alignment ---
    {
        "id": "rss:alignment-forum",
        "name": "Alignment Forum",
        "url": "https://www.alignmentforum.org/feed.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:lesswrong-ai",
        "name": "LessWrong AI Tag",
        "url": "https://www.lesswrong.com/feed.xml?view=tagFeed&tagSlug=ai",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- HuggingFace ---
    {
        "id": "rss:hf-blog",
        "name": "HuggingFace Blog",
        "url": "https://huggingface.co/blog/feed.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:hf-papers-gh",
        "name": "HF Daily Papers (GitHub Actions feed)",
        "url": "https://raw.githubusercontent.com/huangboming/huggingface-daily-paper-feed/refs/heads/main/feed.xml",
        "tier": "tier1",
        "poll": 86400,
    },
    # --- Product Hunt ---
    {
        "id": "rss:producthunt",
        "name": "Product Hunt Feed",
        "url": "https://www.producthunt.com/feed?category=undefined",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- GitHub Trending (better RSS than our scraper) ---
    {
        "id": "rss:gh-trending-python",
        "name": "GitHub Trending Python",
        "url": "https://mshibanami.github.io/GitHubTrendingRSS/daily/python.xml",
        "tier": "tier1",
        "poll": 86400,
    },
    {
        "id": "rss:gh-trending-all",
        "name": "GitHub Trending All",
        "url": "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- Vercel blog RSS ---
    {
        "id": "rss:vercel-blog",
        "name": "Vercel Blog",
        "url": "https://vercel.com/blog",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Bluesky individual accounts (profile RSS) ---
    {
        "id": "rss:bsky-karpathy",
        "name": "Andrej Karpathy (Bluesky)",
        "url": "https://bsky.app/profile/karpathy.bsky.social/rss",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:bsky-simonwillison",
        "name": "Simon Willison (Bluesky)",
        "url": "https://bsky.app/profile/simonwillison.net/rss",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:bsky-swyx",
        "name": "swyx (Bluesky)",
        "url": "https://bsky.app/profile/swyx.io/rss",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Mastodon ---
    {
        "id": "rss:mastodon-simonwillison",
        "name": "Simon Willison (Mastodon)",
        "url": "https://fedi.simonwillison.net/@simon.rss",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- YouTube additional channels ---
    {
        "id": "rss:yt-matt-wolfe",
        "name": "Matt Wolfe YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UChpleBmo18P08aKCIgti38g",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:yt-prompt-eng",
        "name": "Prompt Engineering YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCDq7SjbgRKty5TgGafW8Clg",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- TLDR newsletters (hidden API RSS) ---
    {
        "id": "rss:tldr-ai",
        "name": "TLDR AI Newsletter",
        "url": "https://tldr.tech/api/rss/ai",
        "tier": "tier1",
        "poll": 86400,
    },
    {
        "id": "rss:tldr-tech",
        "name": "TLDR Tech Newsletter",
        "url": "https://tldr.tech/api/rss/tech",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- Previously "email-only" newsletters (RSS found on deeper investigation) ---
    {
        "id": "rss:the-neuron",
        "name": "The Neuron Daily (Atom)",
        "url": "https://www.theneuron.ai/newsletter/feed/",
        "tier": "tier1",
        "poll": 86400,
    },
    {
        "id": "rss:alpha-signal",
        "name": "Alpha Signal (Substack)",
        "url": "https://alphasignalai.substack.com/feed",
        "tier": "tier1",
        "poll": 86400,
    },
    # --- Olshansk community RSS (scraped from sites without native feeds) ---
    {
        "id": "rss:the-batch-3p",
        "name": "The Batch - Andrew Ng (3rd party RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_the_batch.xml",
        "tier": "tier1",
        "poll": 86400,
    },
    {
        "id": "rss:google-ai-3p",
        "name": "Google AI Blog (3rd party RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_google_ai.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:ollama-3p",
        "name": "Ollama Blog (3rd party RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_ollama.xml",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:openai-research-3p",
        "name": "OpenAI Research (3rd party RSS)",
        "url": "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_openai_research.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Batch 3: sources with native RSS (replaces scrapers where possible) ---
    {
        "id": "rss:openai-blog",
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog/rss.xml",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:google-ai-research",
        "name": "Google AI Research Blog",
        "url": "https://research.google/blog/rss",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:google-cloud-ai",
        "name": "Google Cloud AI/ML Blog",
        "url": "https://cloudblog.withgoogle.com/topics/ai-machine-learning/rss/",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:meta-engineering",
        "name": "Meta Engineering Blog",
        "url": "https://engineering.fb.com/feed/",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:cursor-changelog-anyfeed",
        "name": "Cursor Changelog (any-feeds)",
        "url": "https://any-feeds.com/api/feeds/custom/cmkoaiogm0000lf04qmtirq2g/rss.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:tabnine-blog-rss",
        "name": "Tabnine Blog (RSS)",
        "url": "https://www.tabnine.com/blog/feed/",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:amazon-q-notes",
        "name": "Amazon Q Developer Release Notes",
        "url": "https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/doc-history.rss",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:vercel-atom",
        "name": "Vercel Atom Feed",
        "url": "https://vercel.com/atom",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Batch 3: additional PyPI packages ---
    {
        "id": "rss:pypi-langchain-community",
        "name": "PyPI langchain-community Releases",
        "url": "https://pypi.org/rss/project/langchain-community/releases.xml",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:pypi-dspy-ai",
        "name": "PyPI dspy-ai Releases",
        "url": "https://pypi.org/rss/project/dspy-ai/releases.xml",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- Batch 3: additional subreddits ---
    {
        "id": "rss:reddit-cursor",
        "name": "r/cursor",
        "url": "https://www.reddit.com/r/cursor/.rss",
        "tier": "tier1",
        "poll": 1800,
    },
    {
        "id": "rss:reddit-windsurf",
        "name": "r/Windsurf",
        "url": "https://www.reddit.com/r/Windsurf/.rss",
        "tier": "tier1",
        "poll": 1800,
    },
    # --- Batch 3: additional SO tags ---
    {
        "id": "rss:so-ai-assistant",
        "name": "SO ai-assistant Tag",
        "url": "https://stackoverflow.com/feeds/tag?tagnames=ai-assistant",
        "tier": "tier3",
        "poll": 86400,
    },
    # --- Batch 3: Hashnode ---
    {
        "id": "rss:hashnode-ai",
        "name": "Hashnode AI Tag",
        "url": "https://hashnode.com/n/ai/rss",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Batch 3: HN combined keyword ---
    {
        "id": "rss:hn-ai-llm-agent",
        "name": "HN AI+LLM+Agent Keywords",
        "url": "https://hnrss.org/newest?q=AI+OR+LLM+OR+coding+agent",
        "tier": "tier1",
        "poll": 1800,
    },
    # --- Batch 3: Mastodon ---
    {
        "id": "rss:mastodon-llm",
        "name": "Mastodon #LLM Tag",
        "url": "https://mastodon.social/tags/LLM.rss",
        "tier": "tier3",
        "poll": 3600,
    },
    # --- Batch 3: additional newsletters ---
    {
        "id": "rss:algorithmic-bridge",
        "name": "The Algorithmic Bridge",
        "url": "https://thealgorithmicbridge.substack.com/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:one-useful-thing",
        "name": "One Useful Thing (Ethan Mollick)",
        "url": "https://www.oneusefulthing.org/feed",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Batch 3: HuggingFace Papers official ---
    # --- Batch 3: GitHub discussions ---
    {
        "id": "rss:gh-cursor-discussions",
        "name": "Cursor GitHub Discussions",
        "url": "https://github.com/getcursor/cursor/discussions.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Batch 3: MCP spec (specification repo, different from protocol repo) ---
    {
        "id": "rss:gh-mcp-specification",
        "name": "MCP Specification Releases",
        "url": "https://github.com/modelcontextprotocol/specification/releases.atom",
        "tier": "tier1",
        "poll": 3600,
    },
    # --- Batch 3: awesome-generative-ai ---
    {
        "id": "rss:awesome-gen-ai",
        "name": "awesome-generative-ai Commits",
        "url": "https://github.com/steven2358/awesome-generative-ai/commits/main.atom",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- Batch 3: Product Hunt AI category (correct URL) ---
    {
        "id": "rss:producthunt-ai",
        "name": "Product Hunt AI Category",
        "url": "https://www.producthunt.com/feed?category=artificial-intelligence",
        "tier": "tier2",
        "poll": 3600,
    },
    # --- Batch 3: YouTube channels ---
    {
        "id": "rss:yt-two-minute-papers",
        "name": "Two Minute Papers YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:yt-karpathy",
        "name": "Andrej Karpathy YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCPkY9N2bE_yK3s_0kXvj-0A",
        "tier": "tier1",
        "poll": 3600,
    },
    {
        "id": "rss:yt-matthew-berman",
        "name": "Matthew Berman YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCawZsQWqfGSbCI5yjkdVkTA",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:yt-sentdex",
        "name": "Sentdex YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCfzlCWGWYyIQ0aLC5w48gBQ",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:yt-deeplearning-ai",
        "name": "DeepLearning.AI YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCcIXc5mJsHVYTZR1maL5l9w",
        "tier": "tier2",
        "poll": 86400,
    },
    # --- arXiv category RSS feeds (direct, complements our API adapter) ---
    {
        "id": "rss:arxiv-cs-cl",
        "name": "arXiv cs.CL (Computation & Language)",
        "url": "https://export.arxiv.org/rss/cs.CL",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:arxiv-cs-se",
        "name": "arXiv cs.SE (Software Engineering)",
        "url": "https://export.arxiv.org/rss/cs.SE",
        "tier": "tier1",
        "poll": 86400,
    },
    {
        "id": "rss:arxiv-cs-pl",
        "name": "arXiv cs.PL (Programming Languages)",
        "url": "https://export.arxiv.org/rss/cs.PL",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:arxiv-cs-cr",
        "name": "arXiv cs.CR (Security & Cryptography)",
        "url": "https://export.arxiv.org/rss/cs.CR",
        "tier": "tier3",
        "poll": 86400,
    },
]

# ===========================================================================
# Playwright scraper targets — blogs/changelogs without RSS
# ===========================================================================

SCRAPER_SOURCES = [
    # --- LLM provider changelogs ---
    {
        "id": "scraper:claude-release-notes",
        "name": "Claude Platform Release Notes",
        "url": "https://platform.claude.com/docs/en/release-notes/overview",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, section, div.release-note",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time, span.date",
                "excerpt": "p",
            },
            "seen_urls": [],
            "wait_for_selector": "article, section",
        },
    },
    {
        "id": "scraper:claude-code-changelog",
        "name": "Claude Code Changelog",
        "url": "https://code.claude.com/docs/en/changelog",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, section, div.changelog-entry",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
            "wait_for_selector": "article, section",
        },
    },
    {
        "id": "scraper:gemini-api-changelog",
        "name": "Gemini API Changelog",
        "url": "https://ai.google.dev/gemini-api/docs/changelog",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, section, div.devsite-article-body h2",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
            "wait_for_selector": "article",
        },
    },
    {
        "id": "scraper:mistral-changelog",
        "name": "Mistral Docs Changelog",
        "url": "https://docs.mistral.ai/getting-started/changelog",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, section, h2",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:mistral-news",
        "name": "Mistral News",
        "url": "https://mistral.ai/news",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, a[href*='/news/']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:cohere-changelog",
        "name": "Cohere Changelog",
        "url": "https://docs.cohere.com/changelog",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, section, div.changelog-entry",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:xai-release-notes",
        "name": "xAI Developer Release Notes",
        "url": "https://docs.x.ai/developers/release-notes",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, section",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:meta-ai-blog",
        "name": "AI at Meta Blog",
        "url": "https://ai.meta.com/blog/",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, a[href*='/blog/']",
                "title": "h2, h3, span.title",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    # --- AI coding tools changelogs ---
    {
        "id": "scraper:cursor-changelog",
        "name": "Cursor Changelog",
        "url": "https://cursor.com/changelog",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, section, div.changelog-entry",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
            "wait_for_selector": "article, section",
        },
    },
    {
        "id": "scraper:windsurf-changelog",
        "name": "Windsurf Changelog",
        "url": "https://windsurf.com/changelog",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, section",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:windsurf-vscode-changelog",
        "name": "Windsurf VS Code Extension Changelog",
        "url": "https://windsurf.com/changelog/vscode",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, section",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:continue-changelog",
        "name": "Continue Changelog",
        "url": "https://changelog.continue.dev/",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, section, div.changelog-entry",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:devin-release-notes",
        "name": "Devin Release Notes",
        "url": "https://docs.devin.ai/release-notes/overview",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, section",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:devin-api-notes",
        "name": "Devin API Release Notes",
        "url": "https://docs.devin.ai/api-reference/release-notes",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, section",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:aider-history",
        "name": "Aider Release History",
        "url": "https://aider.chat/HISTORY.html",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "h2, h3",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "excerpt": "p, ul",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:tabnine-release-notes",
        "name": "Tabnine Release Notes",
        "url": "https://docs.tabnine.com/main/administering-tabnine/release-notes",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, section",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:vercel-changelog",
        "name": "Vercel Changelog",
        "url": "https://vercel.com/changelog",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, section, a[href*='/changelog/']",
                "title": "h2, h3",
                "url": "a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:v0-changelog",
        "name": "v0 Changelog",
        "url": "https://v0.app/changelog",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, section",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    # --- MCP ecosystem pages ---
    {
        "id": "scraper:mcp-spec-page",
        "name": "MCP Specification Page",
        "url": "https://modelcontextprotocol.io/specification/2025-06-18",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, section, main",
                "title": "h1, h2",
                "url": "h1 a, h2 a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:smithery-registry",
        "name": "Smithery MCP Registry",
        "url": "https://smithery.ai/",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "a[href*='/server/']",
                "title": "h3, h4, span",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:pulsemcp",
        "name": "PulseMCP Homepage",
        "url": "https://www.pulsemcp.com/",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, section, a[href*='/server']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:hf-daily-papers",
        "name": "HuggingFace Daily Papers Page",
        "url": "https://huggingface.co/papers",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, a[href*='/papers/']",
                "title": "h3, h4, span",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    # --- Framework/tool blogs ---
    {
        "id": "scraper:langchain-blog",
        "name": "LangChain Blog",
        "url": "https://blog.langchain.com/",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, a[href*='/blog/']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:llamaindex-blog",
        "name": "LlamaIndex Blog",
        "url": "https://www.llamaindex.ai/blog",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, a[href*='/blog/']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:cognition-blog",
        "name": "Cognition/Devin Blog",
        "url": "https://cognition.ai/blog",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, a[href*='/blog/']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:replit-blog",
        "name": "Replit Blog",
        "url": "https://blog.replit.com/",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, a[href*='/blog/']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:lovable-blog",
        "name": "Lovable Blog",
        "url": "https://lovable.dev/blog",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, a[href*='/blog/']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:windsurf-blog",
        "name": "Windsurf Blog",
        "url": "https://windsurf.com/blog",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, a[href*='/blog/']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:semantic-kernel-blog",
        "name": "Semantic Kernel Blog",
        "url": "https://devblogs.microsoft.com/semantic-kernel/",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article",
                "title": "h2 a",
                "url": "h2 a",
                "date": "time",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    # --- LLM Benchmarks/Leaderboards ---
    {
        "id": "scraper:lmsys-arena",
        "name": "LMSYS Chatbot Arena",
        "url": "https://lmarena.ai/",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "div.leaderboard-row, tr",
                "title": "td, span.model-name",
                "url": "a",
                "excerpt": "td",
            },
            "seen_urls": [],
            "wait_for_selector": "table, div.leaderboard",
        },
    },
    {
        "id": "scraper:artificial-analysis",
        "name": "Artificial Analysis Models",
        "url": "https://artificialanalysis.ai/leaderboards/models",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "tr, div.model-card",
                "title": "td, span",
                "url": "a",
                "excerpt": "td",
            },
            "seen_urls": [],
        },
    },
    # --- Product Hunt AI categories ---
    {
        "id": "scraper:ph-ai-dev-tools",
        "name": "Product Hunt AI Developer Tools",
        "url": "https://www.producthunt.com/categories/ai-developer-tools",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "div[data-test='post-item'], a[href*='/posts/']",
                "title": "h3, strong",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    # --- MCP additional directories ---
    {
        "id": "scraper:mcpservers-org",
        "name": "MCP Servers Directory",
        "url": "https://mcpservers.org/",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "article, a[href*='/server']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    {
        "id": "scraper:glama-mcp",
        "name": "Glama MCP Directory",
        "url": "https://glama.ai/mcp/servers",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": "a[href*='/mcp/servers/']",
                "title": "h3, h4, span",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
    # --- GitHub Trending (scraper for momentum detection) ---
    {
        "id": "scraper:github-trending",
        "name": "GitHub Trending Repos",
        "url": "https://github.com/trending",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article.Box-row",
                "title": "h2 a",
                "url": "h2 a",
                "excerpt": "p",
            },
            "seen_urls": [],
            "wait_for_selector": "article.Box-row",
            "post_process": "github_trending",
        },
    },
    # --- Claude MCP Marketplace (curated MCP servers) ---
    {
        "id": "scraper:claude-mcp-marketplace",
        "name": "Claude MCP Marketplace",
        "url": "https://claude.ai/integrations",
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "[data-testid='integration-card'], .integration-card, article",
                "title": "h3, h2, [data-testid='integration-name']",
                "url": "a[href]",
                "excerpt": "p, [data-testid='integration-description']",
            },
            "seen_urls": [],
            "wait_for_selector": "[data-testid='integration-card'], .integration-card, article",
        },
    },
    # --- MCP Official Integrations (modelcontextprotocol.io) ---
    {
        "id": "scraper:mcp-official-integrations",
        "name": "MCP Official Integrations",
        "url": "https://modelcontextprotocol.io/integrations",
        "tier": "tier2",
        "config": {
            "selectors": {
                "item": ".integration-card, article, [data-integration]",
                "title": "h3, h2, .name",
                "url": "a[href]",
                "excerpt": "p, .description",
            },
            "seen_urls": [],
            "wait_for_selector": ".integration-card, article",
        },
    },
]

# ===========================================================================
# Deep GitHub repos to watch (new additions)
# ===========================================================================

GITHUB_DEEP_SOURCES = [
    {
        "id": "github-deep:langchain-ai/langchain",
        "name": "langchain-ai/langchain (deep)",
        "url": "https://github.com/langchain-ai/langchain",
        "config": {
            "star_milestones": [50000, 100000],
            "commit_burst_threshold": 30,
            "watched_files": ["CHANGELOG.md"],
        },
    },
    {
        "id": "github-deep:crewAIInc/crewAI",
        "name": "crewAIInc/crewAI (deep)",
        "url": "https://github.com/crewAIInc/crewAI",
        "config": {
            "star_milestones": [10000, 25000],
            "commit_burst_threshold": 20,
            "watched_files": ["CHANGELOG.md"],
        },
    },
    {
        "id": "github-deep:continuedev/continue",
        "name": "continuedev/continue (deep)",
        "url": "https://github.com/continuedev/continue",
        "config": {
            "star_milestones": [10000, 25000],
            "commit_burst_threshold": 20,
            "watched_files": ["CHANGELOG.md"],
        },
    },
    {
        "id": "github-deep:cline/cline",
        "name": "cline/cline (deep)",
        "url": "https://github.com/cline/cline",
        "config": {
            "star_milestones": [10000, 25000],
            "commit_burst_threshold": 20,
            "watched_files": ["CHANGELOG.md"],
        },
    },
    {
        "id": "github-deep:RooCodeInc/Roo-Code",
        "name": "RooCodeInc/Roo-Code (deep)",
        "url": "https://github.com/RooCodeInc/Roo-Code",
        "config": {
            "star_milestones": [5000, 10000],
            "commit_burst_threshold": 15,
            "watched_files": ["CHANGELOG.md"],
        },
    },
    {
        "id": "github-deep:Aider-AI/aider",
        "name": "Aider-AI/aider (deep)",
        "url": "https://github.com/Aider-AI/aider",
        "config": {
            "star_milestones": [10000, 25000],
            "commit_burst_threshold": 20,
            "watched_files": ["HISTORY.md"],
        },
    },
    {
        "id": "github-deep:pydantic/pydantic-ai",
        "name": "pydantic/pydantic-ai (deep)",
        "url": "https://github.com/pydantic/pydantic-ai",
        "config": {
            "star_milestones": [5000, 10000],
            "commit_burst_threshold": 15,
            "watched_files": ["CHANGELOG.md"],
        },
    },
]

# ===========================================================================
# Bluesky additional searches
# ===========================================================================

BLUESKY_SOURCES = [
    {
        "id": "bluesky:search-cursor-ide",
        "name": "Bluesky Cursor IDE Search",
        "url": "https://bsky.app/search?q=cursor+ide",
        "tier": "tier2",
        "poll": 1800,
    },
    {
        "id": "bluesky:search-copilot",
        "name": "Bluesky GitHub Copilot Search",
        "url": "https://bsky.app/search?q=github+copilot",
        "tier": "tier2",
        "poll": 1800,
    },
    {
        "id": "bluesky:search-ai-coding",
        "name": "Bluesky AI Coding Search",
        "url": "https://bsky.app/search?q=ai+coding+agent",
        "tier": "tier2",
        "poll": 1800,
    },
    {
        "id": "bluesky:search-cline",
        "name": "Bluesky Cline Search",
        "url": "https://bsky.app/search?q=cline+vscode",
        "tier": "tier3",
        "poll": 3600,
    },
]

# ===========================================================================
# Awesome-list sources (git-based, full README scrape via ingest_awesome.py)
# ===========================================================================

AWESOME_LIST_SOURCES = [
    {
        "id": "awesome:punkpeye/awesome-mcp-servers",
        "name": "awesome-mcp-servers (full scrape)",
        "url": "https://github.com/punkpeye/awesome-mcp-servers",
        "poll": 86400,
        "tier": "tier1",
        "config": {
            "repo_url": "https://github.com/punkpeye/awesome-mcp-servers",
        },
    },
    {
        "id": "awesome:travisvn/awesome-claude-skills",
        "name": "awesome-claude-skills (full scrape)",
        "url": "https://github.com/travisvn/awesome-claude-skills",
        "poll": 86400,
        "tier": "tier1",
        "config": {
            "repo_url": "https://github.com/travisvn/awesome-claude-skills",
        },
    },
    {
        "id": "awesome:punkpeye/awesome-mcp-clients",
        "name": "awesome-mcp-clients (full scrape)",
        "url": "https://github.com/punkpeye/awesome-mcp-clients",
        "poll": 86400,
        "tier": "tier2",
        "config": {
            "repo_url": "https://github.com/punkpeye/awesome-mcp-clients",
        },
    },
    {
        "id": "awesome:steven2358/awesome-generative-ai",
        "name": "awesome-generative-ai (full scrape)",
        "url": "https://github.com/steven2358/awesome-generative-ai",
        "poll": 86400,
        "tier": "tier2",
        "config": {
            "repo_url": "https://github.com/steven2358/awesome-generative-ai",
        },
    },
]

# ===========================================================================
# YouTube channels (resolve channel IDs and use RSS adapter)
# ===========================================================================

YOUTUBE_SOURCES = [
    {
        "id": "rss:yt-fireship",
        "name": "Fireship YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCsBjURrPoezykLs9EqgamOA",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:yt-theprimeagen",
        "name": "ThePrimeagen YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC8ENHE5xdFSwx71u3fDH5Xw",
        "tier": "tier2",
        "poll": 3600,
    },
    {
        "id": "rss:yt-github",
        "name": "GitHub YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC7c3Kb6jYCRj4JOHHZTxKsQ",
        "tier": "tier2",
        "poll": 86400,
    },
    {
        "id": "rss:yt-vercel",
        "name": "Vercel YouTube",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCDPkJI7n7eVpdBESaelbbyw",
        "tier": "tier3",
        "poll": 86400,
    },
]

# ===========================================================================
# Sitemap: additional docs sites
# ===========================================================================

SITEMAP_SOURCES = [
    {
        "id": "sitemap:mistral-docs",
        "name": "Mistral AI Documentation",
        "url": "https://docs.mistral.ai/sitemap.xml",
        "tier": "tier2",
        "config": {"url_filter": "/getting-started/,/capabilities/,/api/"},
    },
    {
        "id": "sitemap:cohere-docs",
        "name": "Cohere Documentation",
        "url": "https://docs.cohere.com/sitemap.xml",
        "tier": "tier2",
        "config": {"url_filter": "/docs/,/changelog"},
    },
    {
        "id": "sitemap:dspy-weekly",
        "name": "DSPy Weekly",
        "url": "https://dspyweekly.com/sitemap.xml",
        "tier": "tier2",
        "config": {},
    },
    {
        "id": "sitemap:claudelog",
        "name": "ClaudeLog (Curated Claude Code Best Practices)",
        "url": "https://claudelog.com/sitemap.xml",
        "tier": "tier1",
        "config": {},
    },
    {
        "id": "sitemap:mcp-spec",
        "name": "MCP Protocol Specification",
        "url": "https://modelcontextprotocol.io/sitemap.xml",
        "tier": "tier1",
        "config": {},
    },
]

# ===========================================================================
# Main seed function
# ===========================================================================

ALL_SOURCES = []

# Build RSS list with standard fields
for s in RSS_SOURCES:
    ALL_SOURCES.append(
        {
            "id": s["id"],
            "name": s["name"],
            "type": "rss",
            "url": s["url"],
            "poll_interval_seconds": s["poll"],
            "tier": s["tier"],
            "config": {},
        }
    )

# Build scraper list
for s in SCRAPER_SOURCES:
    ALL_SOURCES.append(
        {
            "id": s["id"],
            "name": s["name"],
            "type": "scraper",
            "url": s["url"],
            "poll_interval_seconds": 21600,  # every 6 hours
            "tier": s.get("tier", "tier2"),
            "config": s["config"],
        }
    )

# Build github-deep list
for s in GITHUB_DEEP_SOURCES:
    ALL_SOURCES.append(
        {
            "id": s["id"],
            "name": s["name"],
            "type": "github-deep",
            "url": s["url"],
            "poll_interval_seconds": 1800,
            "tier": "tier1",
            "config": s["config"],
        }
    )

# Build bluesky list
for s in BLUESKY_SOURCES:
    ALL_SOURCES.append(
        {
            "id": s["id"],
            "name": s["name"],
            "type": "bluesky",
            "url": s["url"],
            "poll_interval_seconds": s["poll"],
            "tier": s["tier"],
            "config": {},
        }
    )

# Build awesome-list sources
for s in AWESOME_LIST_SOURCES:
    ALL_SOURCES.append(
        {
            "id": s["id"],
            "name": s["name"],
            "type": "awesome-list",
            "url": s["url"],
            "poll_interval_seconds": s["poll"],
            "tier": s["tier"],
            "config": s["config"],
        }
    )

# Build YouTube list (uses RSS adapter)
for s in YOUTUBE_SOURCES:
    ALL_SOURCES.append(
        {
            "id": s["id"],
            "name": s["name"],
            "type": "rss",
            "url": s["url"],
            "poll_interval_seconds": s["poll"],
            "tier": s["tier"],
            "config": {},
        }
    )

# Build sitemap list
for s in SITEMAP_SOURCES:
    ALL_SOURCES.append(
        {
            "id": s["id"],
            "name": s["name"],
            "type": "sitemap",
            "url": s["url"],
            "poll_interval_seconds": 43200,  # twice daily
            "tier": s["tier"],
            "config": s["config"],
        }
    )


async def seed() -> None:
    await init_db()

    async with _db.async_session_factory() as session:
        inserted = 0
        skipped = 0

        for spec in ALL_SOURCES:
            result = await session.execute(
                select(Source).where(Source.id == spec["id"])
            )
            existing = result.scalar_one_or_none()

            if existing is not None:
                skipped += 1
                continue

            source = Source(
                id=spec["id"],
                name=spec["name"],
                type=spec["type"],
                url=spec["url"],
                is_active=True,
                poll_interval_seconds=spec["poll_interval_seconds"],
                tier=spec["tier"],
                config=spec["config"],
            )
            session.add(source)
            print(f"  ADD   {spec['id']}")
            inserted += 1

        await session.commit()

        # Summary by type
        from collections import Counter

        type_counts = Counter(s["type"] for s in ALL_SOURCES)
        print(f"\n{'='*60}")
        print(f"Source expansion complete: {inserted} added, {skipped} skipped")
        print(f"Total defined: {len(ALL_SOURCES)}")
        print(f"\nBy adapter type:")
        for t, c in sorted(type_counts.items()):
            print(f"  {t}: {c}")

    await close_db()


if __name__ == "__main__":
    asyncio.run(seed())
