"""
Seed script for reference_items table.

Populates the reference set with ~125 manually curated items (~95 positive, ~30 negative).
The reference set is used by the relevance gate to calibrate cosine-similarity scoring.

Usage:
    python scripts/seed_reference_set.py

Idempotent: re-running skips URLs already in the database.
"""
import asyncio
import sys

sys.path.insert(0, ".")  # Allow running from project root

import src.core.init_db as _db
from src.core.init_db import init_db
from src.core.config import get_settings
from src.services.llm_client import LLMClient
from src.services.spend_tracker import SpendTracker
from src.services.pipeline_helpers import build_embed_input
from src.models.models import ReferenceItem
import redis.asyncio as aioredis
from sqlalchemy import select, text

# ---------------------------------------------------------------------------
# Reference set: ~95 positive (relevant) + ~30 negative (noise)
# ---------------------------------------------------------------------------

REFERENCE_ITEMS = [
    # -------------------------------------------------------------------------
    # POSITIVE ITEMS: Claude Code, MCP, Anthropic, skills, hooks, workflows
    # -------------------------------------------------------------------------
    # --- Claude Code Official (5) ---
    {
        "url": "https://github.com/anthropics/claude-code",
        "title": "Claude Code - An agentic coding tool by Anthropic",
        "description": "Official GitHub repository for Claude Code, Anthropic's AI-powered coding assistant that runs in your terminal.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview",
        "title": "Claude Code Overview — Official documentation",
        "description": "Official Anthropic documentation covering Claude Code capabilities, setup, and key concepts.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/releases",
        "title": "Claude Code Releases — Version history and changelogs",
        "description": "All Claude Code release notes, version history, and feature changelogs on GitHub.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.anthropic.com/engineering/claude-code-best-practices",
        "title": "Claude Code Best Practices — Anthropic Engineering Blog",
        "description": "Anthropic engineering team shares best practices for effective Claude Code usage in real workflows.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/tutorials",
        "title": "Claude Code Tutorials — Getting started guides",
        "description": "Step-by-step tutorials for Claude Code from the official Anthropic documentation.",
        "is_positive": True,
        "label": "positive",
    },
    # --- Anthropic Model Updates (8) ---
    {
        "url": "https://docs.anthropic.com/en/docs/about-claude/models",
        "title": "Claude Model Documentation — Model specifications and capabilities",
        "description": "Comprehensive documentation of all Claude models including context windows, pricing, and capabilities.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.anthropic.com/news/claude-3-5-sonnet",
        "title": "Claude 3.5 Sonnet Release — Model announcement",
        "description": "Anthropic's announcement of Claude 3.5 Sonnet, featuring improved intelligence and coding capabilities.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.anthropic.com/news/claude-3-5-haiku",
        "title": "Claude 3.5 Haiku Release — Fast, affordable model",
        "description": "Anthropic launches Claude 3.5 Haiku, the fastest and most compact model in the Claude 3.5 family.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.anthropic.com/en/docs/build-with-claude/tool-use",
        "title": "Tool Use Documentation — Function calling with Claude",
        "description": "Official guide to implementing tool use (function calling) with the Claude API.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.anthropic.com/en/api/messages",
        "title": "Messages API Reference — Claude API endpoint",
        "description": "Complete API reference for Anthropic's Messages API used to interact with Claude models.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.anthropic.com/news/extended-thinking",
        "title": "Extended Thinking — Deep reasoning capability",
        "description": "Anthropic introduces extended thinking mode, enabling Claude to reason through complex problems step by step.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching",
        "title": "Prompt Caching — Reduce latency and cost",
        "description": "Guide to Anthropic's prompt caching feature that reduces costs for repeated context in API calls.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.anthropic.com/research/building-effective-agents",
        "title": "Building Effective Agents — Anthropic Research",
        "description": "Anthropic research blog post on patterns and practices for building effective AI agents with Claude.",
        "is_positive": True,
        "label": "positive",
    },
    # --- MCP Protocol (5) ---
    {
        "url": "https://modelcontextprotocol.io/introduction",
        "title": "Model Context Protocol Introduction — Open standard for AI tools",
        "description": "Introduction to the Model Context Protocol (MCP), an open standard for connecting AI models to external tools and data sources.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/modelcontextprotocol/specification",
        "title": "MCP Specification — Protocol definition and reference",
        "description": "Official specification repository for the Model Context Protocol, including schemas and protocol details.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://modelcontextprotocol.io/quickstart/server",
        "title": "Building MCP Servers — Quickstart guide",
        "description": "Official quickstart guide for building MCP servers to expose tools and data to AI models.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://modelcontextprotocol.io/quickstart/client",
        "title": "Building MCP Clients — Quickstart guide",
        "description": "Official quickstart guide for building MCP clients that connect to MCP servers.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/modelcontextprotocol/servers",
        "title": "Official MCP Servers — Reference implementations",
        "description": "Collection of official MCP server reference implementations maintained by the MCP team.",
        "is_positive": True,
        "label": "positive",
    },
    # --- MCP Server Implementations (15) ---
    {
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        "title": "MCP Filesystem Server — Local file operations",
        "description": "Official MCP server that exposes local filesystem operations (read, write, list) to AI models.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
        "title": "MCP Git Server — Repository operations",
        "description": "Official MCP server providing git repository operations including log, diff, and commit history.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
        "title": "MCP Postgres Server — Database operations",
        "description": "Official MCP server for PostgreSQL database queries and schema inspection.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        "title": "MCP Memory Server — Persistent memory for AI",
        "description": "Official MCP server that provides persistent key-value memory storage for AI assistants.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
        "title": "MCP Brave Search Server — Web search integration",
        "description": "Official MCP server integrating Brave Search API for real-time web search in AI tools.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer",
        "title": "MCP Puppeteer Server — Browser automation",
        "description": "Official MCP server for browser automation using Puppeteer — enables web scraping and interaction.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
        "title": "MCP Slack Server — Team messaging integration",
        "description": "Official MCP server for Slack integration — send messages, read channels, and manage workspace.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/upstash/context7",
        "title": "Context7 — MCP server for up-to-date library documentation",
        "description": "MCP server that provides current library documentation and API references to AI coding assistants.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/punkpeye/awesome-mcp-servers",
        "title": "Awesome MCP Servers — Community-curated MCP server list",
        "description": "Comprehensive community-maintained list of MCP servers across all categories and use cases.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-cookbook/tree/main/misc/mcp",
        "title": "MCP Cookbook — Example MCP implementations",
        "description": "Anthropic cookbook examples showing how to build and use MCP servers with Claude.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/nicobailon/mcp-server-perplexity-ask",
        "title": "Perplexity MCP Server — AI-powered web search",
        "description": "MCP server that integrates Perplexity AI for intelligent web search with citations.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/calclavia/mcp-playwright",
        "title": "Playwright MCP Server — Browser testing automation",
        "description": "MCP server for Playwright-based browser automation, enabling AI-driven web testing and scraping.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/designcomputer/mcp-pandoc",
        "title": "Pandoc MCP Server — Document conversion",
        "description": "MCP server wrapping Pandoc for document format conversion across markdown, PDF, Word, and HTML.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/executeautomation/mcp-playwright",
        "title": "MCP Playwright — Browser automation for testing",
        "description": "MCP server providing Playwright browser automation capabilities for AI-assisted testing workflows.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/tavily-ai/tavily-mcp",
        "title": "Tavily MCP Server — AI search integration",
        "description": "MCP server for Tavily's AI-optimized search engine, designed for accurate and relevant results.",
        "is_positive": True,
        "label": "positive",
    },
    # --- Claude Code Skills and Hooks (15) ---
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/hooks.md",
        "title": "Claude Code Hooks — Event-driven automation",
        "description": "Documentation for Claude Code hooks — shell scripts triggered by Claude Code lifecycle events.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/mvanhorn/awesome-claude-code",
        "title": "Awesome Claude Code — Community resource collection",
        "description": "Curated community list of Claude Code resources, skills, hooks, and configurations.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/skills.md",
        "title": "Claude Code Skills — Reusable task definitions",
        "description": "Documentation for Claude Code skills system — reusable, shareable task definitions for Claude.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/commands.md",
        "title": "Claude Code Commands — Custom slash commands",
        "description": "Guide to creating and using custom slash commands in Claude Code for workflow automation.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/workflows.md",
        "title": "Claude Code Workflows — Multi-step automation",
        "description": "Documentation for Claude Code workflows enabling complex multi-step automation pipelines.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/steipete/claude-code-settings",
        "title": "Claude Code Settings — Community configuration collection",
        "description": "Community-shared Claude Code settings, hooks, and configurations by developer Peter Steinberger.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/memory.md",
        "title": "Claude Code Memory — CLAUDE.md and project context",
        "description": "Guide to Claude Code's memory system using CLAUDE.md files for persistent project context.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/sub-agents.md",
        "title": "Claude Code Sub-Agents — Parallel task execution",
        "description": "Documentation for Claude Code sub-agents enabling parallel, concurrent task execution.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/mcp.md",
        "title": "Claude Code MCP Integration — Using MCP servers",
        "description": "Guide to integrating MCP servers with Claude Code for extended tool capabilities.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/bedrock.md",
        "title": "Claude Code on Bedrock — AWS deployment guide",
        "description": "Guide to running Claude Code with Amazon Bedrock for enterprise and AWS-hosted deployments.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.youtube.com/watch?v=d-VCpAWbspo",
        "title": "Claude Code Full Tutorial — Getting started video",
        "description": "Comprehensive video tutorial covering Claude Code setup, features, and practical workflows.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/jlowin/fastmcp",
        "title": "FastMCP — High-level Python framework for building MCP servers",
        "description": "Python framework that makes building MCP servers fast and ergonomic with minimal boilerplate.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/github-actions.md",
        "title": "Claude Code GitHub Actions — CI/CD integration",
        "description": "Guide to integrating Claude Code with GitHub Actions for automated code review and CI workflows.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/courses/tree/master/claude-code",
        "title": "Claude Code Course — Official Anthropic training material",
        "description": "Official Anthropic course materials for learning Claude Code through structured exercises.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://skills.sh",
        "title": "skills.sh — Claude Code skill marketplace",
        "description": "Community marketplace for discovering and sharing Claude Code skills and automation workflows.",
        "is_positive": True,
        "label": "positive",
    },
    # --- Additional Claude Code and AI Ecosystem (15) ---
    {
        "url": "https://docs.anthropic.com/en/docs/build-with-claude/computer-use",
        "title": "Claude Computer Use — Desktop automation documentation",
        "description": "Official Anthropic documentation for Claude's computer use capability to control desktop applications.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.anthropic.com/news/claude-3-7-sonnet",
        "title": "Claude 3.7 Sonnet Release — Enhanced reasoning model",
        "description": "Anthropic releases Claude 3.7 Sonnet with improved reasoning and extended thinking capabilities.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.anthropic.com/en/docs/build-with-claude/embeddings",
        "title": "Embeddings Documentation — Semantic search with Claude",
        "description": "Official guide to generating and using embeddings via Anthropic API for semantic search.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/settings.md",
        "title": "Claude Code Settings — Configuration reference",
        "description": "Complete reference for Claude Code settings, environment variables, and configuration options.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://modelcontextprotocol.io/docs/concepts/architecture",
        "title": "MCP Architecture — Protocol design and components",
        "description": "Technical overview of MCP architecture including hosts, clients, servers, and message flow.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://modelcontextprotocol.io/docs/concepts/resources",
        "title": "MCP Resources — Exposing data to AI models",
        "description": "Guide to MCP resources — the mechanism for exposing structured data to AI model contexts.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://modelcontextprotocol.io/docs/concepts/tools",
        "title": "MCP Tools — Enabling AI model actions",
        "description": "Documentation for MCP tools that allow AI models to execute actions through MCP servers.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/ide-integrations.md",
        "title": "Claude Code IDE Integrations — VS Code and JetBrains",
        "description": "Guide to using Claude Code with VS Code, JetBrains IDEs, and other development environments.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.anthropic.com/news/model-context-protocol",
        "title": "Anthropic Announces Model Context Protocol — MCP launch",
        "description": "Anthropic's official announcement of the Model Context Protocol as an open standard for AI tools.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-sdk-python",
        "title": "Anthropic Python SDK — Official client library",
        "description": "Official Python SDK for the Anthropic API with async support and type hints.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-sdk-typescript",
        "title": "Anthropic TypeScript SDK — Official client library",
        "description": "Official TypeScript/JavaScript SDK for the Anthropic API with full type safety.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.anthropic.com/en/docs/build-with-claude/batch-processing",
        "title": "Claude Batch API — Process large volumes efficiently",
        "description": "Documentation for Anthropic's batch processing API for cost-effective large-scale Claude requests.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/claude-code/blob/main/docs/multi-claude.md",
        "title": "Multi-Claude Workflows — Coordinating multiple agents",
        "description": "Patterns and documentation for orchestrating multiple Claude Code instances in parallel workflows.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://modelcontextprotocol.io/docs/concepts/prompts",
        "title": "MCP Prompts — Reusable prompt templates",
        "description": "Documentation for MCP prompts — pre-built templates that servers expose to AI model clients.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.anthropic.com/en/docs/build-with-claude/claude-in-your-ide",
        "title": "Claude in Your IDE — Development environment integration",
        "description": "Guide to integrating Claude AI assistance directly into development environments and code editors.",
        "is_positive": True,
        "label": "positive",
    },
    # --- Anthropic Cookbook Relevant Entries (7) ---
    {
        "url": "https://github.com/anthropics/anthropic-cookbook",
        "title": "Anthropic Cookbook — Code examples and guides",
        "description": "Official Anthropic repository with code examples, tutorials, and implementation guides for Claude.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-cookbook/tree/main/misc/prompt_caching",
        "title": "Prompt Caching Cookbook — Implementation examples",
        "description": "Practical code examples for implementing Anthropic's prompt caching feature to reduce costs.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-cookbook/tree/main/tool_use",
        "title": "Tool Use Cookbook — Function calling examples",
        "description": "Working code examples for tool use and function calling with the Anthropic Claude API.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-cookbook/tree/main/misc/citation",
        "title": "Citation Cookbook — Source attribution examples",
        "description": "Examples for generating citations and source attribution with Claude models.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-cookbook/tree/main/misc/computer_use",
        "title": "Computer Use Cookbook — Screen interaction examples",
        "description": "Code examples for Claude's computer use capability to interact with desktop applications.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-cookbook/tree/main/misc/extended_thinking",
        "title": "Extended Thinking Cookbook — Deep reasoning examples",
        "description": "Practical examples for using Claude's extended thinking mode for complex reasoning tasks.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-cookbook/tree/main/patterns",
        "title": "Patterns Cookbook — Prompt engineering patterns",
        "description": "Curated prompt engineering patterns and best practices from Anthropic's cookbook.",
        "is_positive": True,
        "label": "positive",
    },
    # -------------------------------------------------------------------------
    # COV-03 Expansion: Agent Frameworks, Competing Tools, RAG/Embedding
    # -------------------------------------------------------------------------
    # --- Agent Frameworks (8 items) ---
    {
        "url": "https://github.com/crewAIInc/crewAI",
        "title": "CrewAI — Multi-Agent Orchestration Framework for Python",
        "description": "CrewAI enables Python developers to build multi-agent AI workflows where autonomous agents collaborate to complete complex tasks. Agents have roles, goals, and backstories. Supports tool use, delegation, and sequential/parallel task execution. Popular framework for building production AI agent systems.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/pydantic/pydantic-ai",
        "title": "PydanticAI — Type-Safe Agent Framework by the Pydantic Team",
        "description": "PydanticAI brings Pydantic's type-safety principles to AI agent development. Define agents with structured inputs and outputs, type-checked tools, and dependency injection. Works with OpenAI, Anthropic, Google, and Ollama. Designed for production-grade agents with testable, maintainable code.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/langchain-ai/langchain",
        "title": "LangChain — Framework for LLM-Powered Applications",
        "description": "LangChain is the most widely used framework for building applications powered by large language models. Provides abstractions for chains, agents, memory, and retrieval-augmented generation (RAG). Extensive integration library covers 100+ LLMs, vector stores, and tools. Essential for production LLM applications.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/langchain-ai/langgraph",
        "title": "LangGraph — Stateful Multi-Actor Workflows with LLMs",
        "description": "LangGraph extends LangChain to build stateful, graph-based agent workflows where multiple actors interact over time. Supports cycles, branching, and human-in-the-loop interactions. Enables complex agent architectures like supervisor patterns, plan-and-execute, and reflexion. Built for long-running, production-grade agent systems.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/microsoft/autogen",
        "title": "AutoGen — Microsoft's Framework for Multi-Agent Conversation",
        "description": "AutoGen by Microsoft enables multi-agent conversations where AI agents and humans collaborate to solve complex tasks. Features conversable agents with customizable roles, code execution capabilities, and group chat orchestration. AutoGen AgentChat provides high-level APIs while the core library enables custom agent patterns.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/openai/openai-agents-python",
        "title": "OpenAI Agents SDK — Official Python Framework for Agentic AI",
        "description": "The OpenAI Agents SDK is OpenAI's official Python framework for building agentic AI applications. Provides primitives for agent loops, tool use, handoffs between agents, and guardrails. Integrates with OpenAI's Responses API, supports function calling, and includes tracing for debugging agent runs. Previously known as Swarm.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/anthropics/anthropic-sdk-python/releases",
        "title": "Anthropic Python SDK Releases — Changelog and Version History",
        "description": "Release history for the official Anthropic Python SDK covering new features, bug fixes, and API additions across versions. Tracks Claude API integration changes including Messages API updates, Tool Use improvements, Batch API additions, streaming enhancements, and new model support. Essential for tracking breaking changes in Claude integrations.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/langchain-ai/langsmith-sdk",
        "title": "LangSmith SDK — LLM Observability, Tracing and Evaluation",
        "description": "LangSmith provides observability, tracing, and evaluation tools for LLM applications built with LangChain or any LLM framework. Log traces, evaluate outputs, manage datasets, and run automated tests. Critical for debugging and improving production AI agent performance.",
        "is_positive": True,
        "label": "positive",
    },
    # --- Competing IDE Tools (6 items) ---
    {
        "url": "https://changelog.cursor.com",
        "title": "Cursor Changelog — AI Code Editor Updates",
        "description": "Cursor is an AI-first code editor that deeply integrates GPT-4 and Claude for code generation, editing, and chat. Regular updates add new AI capabilities including multi-file edits, composer mode, background agents, and context management. Competitor to and alternative for GitHub Copilot users.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.blog/changelog/label/copilot/",
        "title": "GitHub Copilot Changelog — Feature Updates and Improvements",
        "description": "GitHub Copilot is Microsoft's AI coding assistant integrated into VS Code, JetBrains, and other editors. Updates add new capabilities like Copilot Chat, workspace context, multi-file edits, and agentic mode. Widely adopted in enterprise environments as the baseline AI coding assistant.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/Aider-AI/aider",
        "title": "Aider — AI Pair Programming in the Terminal",
        "description": "Aider is an open-source AI coding assistant that works in your terminal. Edits files across your whole codebase using git, supports multiple LLMs (Claude, GPT-4, Gemini), and commits changes automatically. Popular alternative to GUI-based AI coding tools for developers who prefer CLI workflows.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.codeium.com/windsurf/getting-started",
        "title": "Windsurf by Codeium — Agentic AI IDE",
        "description": "Windsurf is an agentic AI IDE by Codeium featuring Cascade, a deep contextual AI that understands your entire codebase. Cascade performs multi-file edits, runs terminal commands, and browses the web. Competitor to Cursor with emphasis on multi-step agent flows rather than single-completion assistance.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/openai/codex",
        "title": "OpenAI Codex CLI — Terminal-Based AI Coding Agent",
        "description": "OpenAI Codex CLI is OpenAI's terminal-based AI coding agent (not the deprecated API). Operates like Claude Code for terminal workflows — reads and writes files, runs commands, and iterates on code. Integrates with the Responses API and supports function calling for tool use.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.github.com/en/copilot/github-copilot-in-the-cli",
        "title": "GitHub Copilot CLI — AI-Powered Terminal Commands",
        "description": "GitHub Copilot CLI brings AI to the terminal — suggests shell commands, explains command output, and helps debug CLI errors. Installed via gh extension. Useful for developers who want AI assistance without leaving the terminal. Part of the broader GitHub Copilot ecosystem.",
        "is_positive": True,
        "label": "positive",
    },
    # --- RAG / Embedding / Vector DB (11 items) ---
    {
        "url": "https://docs.voyageai.com/docs/embeddings",
        "title": "Voyage AI Embeddings — State-of-the-Art Embedding Models",
        "description": "Voyage AI provides state-of-the-art embedding models optimized for retrieval tasks. voyage-3.5-lite, voyage-code-3, and voyage-large-2-instruct outperform OpenAI's text-embedding-3-large on standard benchmarks. Voyage embeddings power semantic search, RAG pipelines, and clustering for code and text.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://platform.openai.com/docs/guides/embeddings",
        "title": "OpenAI Embeddings — text-embedding-3-small and text-embedding-3-large",
        "description": "OpenAI's text-embedding-3 model family provides high-performance embeddings for semantic search, RAG, and clustering. text-embedding-3-small offers 62% better performance than ada-002 at lower cost. text-embedding-3-large enables MTEB-leading retrieval. Both support shortened embeddings via dimensions parameter.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.pinecone.io/changelog",
        "title": "Pinecone Changelog — Serverless Vector Database Updates",
        "description": "Pinecone is the leading managed vector database for production RAG applications. Updates add capabilities like sparse+dense hybrid search, namespace-level filtering, metadata filtering, and serverless indexes. Used by LangChain, LlamaIndex, and most major LLM application frameworks as the default vector store.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/weaviate/weaviate",
        "title": "Weaviate — Open-Source Vector Search Engine",
        "description": "Weaviate is an open-source vector search engine with built-in ML model integration, hybrid search, and multi-modal support. Supports text2vec, img2vec, and custom modules. Features GraphQL and REST APIs. Popular for self-hosted RAG deployments and enterprise AI search applications.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/chroma-core/chroma",
        "title": "Chroma — Open-Source Embedding Database",
        "description": "Chroma is an open-source embedding database designed for AI applications. Simple Python API for creating collections, adding documents, and querying by embedding similarity. Default vector store in many LangChain tutorials. Supports persistent storage, client-server mode, and filtering. Used heavily in RAG prototyping.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/qdrant/qdrant",
        "title": "Qdrant — High-Performance Vector Database",
        "description": "Qdrant is a vector database written in Rust optimized for high-performance similarity search. Features filtering at query time (not post-hoc), sparse vector support for hybrid search, binary quantization, and payload indexing. Available as cloud service or self-hosted. Strong performance on ANN benchmarks.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://github.com/run-llama/llama_index",
        "title": "LlamaIndex — Data Framework for LLM-Powered RAG Applications",
        "description": "LlamaIndex is a data framework for connecting LLMs to external data sources via retrieval-augmented generation (RAG). Provides data connectors, index structures, query engines, and agent tools. Supports 100+ data loaders, multi-modal RAG, and agentic RAG patterns. Widely used alternative/complement to LangChain.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.anthropic.com/research/contextual-retrieval",
        "title": "Contextual Retrieval — Anthropic's Approach to Improving RAG Accuracy",
        "description": "Anthropic research on Contextual Retrieval, a technique that prepends chunk-specific context to document chunks before embedding, significantly improving retrieval accuracy. Reduces retrieval failures by 67%. Explains why standard RAG loses context and how to fix it with BM25+contextual embeddings hybrid approaches.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://platform.openai.com/docs/guides/responses-vs-chat-completions",
        "title": "OpenAI Responses API — Stateful Agent-Oriented API",
        "description": "OpenAI's Responses API is a new stateful API designed for agentic applications, replacing parts of the Chat Completions API. Supports built-in tools (web search, code interpreter, file search), multi-turn conversation state management, and reasoning models. Foundation of the OpenAI Agents SDK.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://platform.openai.com/docs/guides/structured-outputs",
        "title": "OpenAI Structured Outputs — Guaranteed JSON Schema Compliance",
        "description": "OpenAI Structured Outputs guarantees that model responses match a provided JSON Schema, eliminating parsing errors in agentic applications. Works with function calling and the Responses API. Critical for production agents that parse LLM output programmatically — enables type-safe integration patterns.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.anthropic.com/en/docs/build-with-claude/tool-use/overview",
        "title": "Claude Tool Use — Function Calling and Agent Tools",
        "description": "Claude's Tool Use (function calling) API enables Claude to use external tools in structured workflows. Define tools as JSON schemas, Claude decides when to call them, parse results, and continue. Foundation of MCP architecture. Supports parallel tool calls, tool_choice forcing, and computer use. Essential for agentic Claude applications.",
        "is_positive": True,
        "label": "positive",
    },
    # -------------------------------------------------------------------------
    # POSITIVE ITEMS: AI-assisted development patterns & intersection content
    # -------------------------------------------------------------------------
    # --- Voyage AI & Embeddings (2) ---
    {
        "url": "https://blog.voyageai.com/2026/01/15/voyage-4/",
        "title": "Voyage 4 Model Family — Shared Embedding Space with MoE Architecture",
        "description": "Voyage AI announces the Voyage 4 embedding model family featuring MoE architecture for 40% lower serving cost, Matryoshka representation learning for flexible dimensions, and a shared embedding space across model tiers enabling mix-and-match indexing.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.voyageai.com/docs/reranker",
        "title": "Voyage AI Reranker — Cross-Encoder Reranking API",
        "description": "Voyage AI reranking API documentation covering rerank-2.5 and rerank-2.5-lite models with 32K token context, instruction-following capability, and production integration patterns for two-stage retrieval pipelines.",
        "is_positive": True,
        "label": "positive",
    },
    # --- AI Coding Gotchas & Patterns (4) ---
    {
        "url": "https://addyo.substack.com/p/the-80-problem-in-agentic-coding",
        "title": "The 80% Problem in Agentic Coding — Why AI Agents Produce Almost-Right Solutions",
        "description": "Analysis of why AI coding agents produce solutions that are 80% correct, covering the debugging paradox, workflow adaptation failures, and the productivity paradox where saved coding time is consumed by coordination overhead with AI agents.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://stackoverflow.blog/2026/01/28/are-bugs-and-incidents-inevitable-with-ai-coding-agents/",
        "title": "Are Bugs and Incidents Inevitable with AI Coding Agents? — Stack Overflow",
        "description": "Stack Overflow blog examining AI code quality data showing 1.5-2x higher bug rates, 8x excessive I/O, and 2x concurrency mistakes in AI-generated code, with recommendations for testing pipelines and review processes.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://addyosmani.com/blog/ai-coding-workflow/",
        "title": "My LLM Coding Workflow Going Into 2026 — Addy Osmani",
        "description": "Practical workflow guide from a senior Google engineer on structuring AI-assisted development, codifying expectations as agent instructions, and treating AI-generated code with the same review rigor as human code.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents",
        "title": "Effective Harnesses for Long-Running Agents — Anthropic Engineering",
        "description": "Anthropic's engineering blog on building robust harnesses for autonomous coding agents covering state management, checkpoint and resume patterns, and orchestration for long-running multi-step development tasks.",
        "is_positive": True,
        "label": "positive",
    },
    # --- Cursor, Copilot, Windsurf, Codex (5) ---
    {
        "url": "https://cursor.com/changelog",
        "title": "Cursor Changelog — AI IDE Release History",
        "description": "Cursor's official changelog tracking every release including cloud agents, background agents, JetBrains integration, MCP Apps with interactive UIs, and subagent parallel execution capabilities.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent",
        "title": "About GitHub Copilot Coding Agent — Official Documentation",
        "description": "Official documentation for GitHub Copilot's autonomous coding agent that can plan, implement, test, and iterate on code changes in pull requests with agent mode for multi-step task execution.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://windsurf.com/changelog",
        "title": "Windsurf Editor Changelog — AI IDE Updates",
        "description": "Official changelog for Windsurf (formerly Codeium) editor tracking Cascade AI improvements, new model support, plugin ecosystem updates, and IDE capability additions for AI-assisted development.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://developers.openai.com/codex/cli",
        "title": "OpenAI Codex CLI — Terminal Coding Agent Documentation",
        "description": "Official documentation for OpenAI's Codex CLI, a terminal-based coding agent that can read, modify, and execute code locally with support for subagents, custom agents, and parallel task execution.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.github.com/en/copilot/tutorials/enhance-agent-mode-with-mcp",
        "title": "Enhancing GitHub Copilot Agent Mode with MCP — Official Tutorial",
        "description": "Official GitHub tutorial on integrating MCP servers with Copilot agent mode, enabling connection to external tools, databases, and data sources during autonomous coding sessions.",
        "is_positive": True,
        "label": "positive",
    },
    # --- Agent Frameworks: PydanticAI, DSPy, Semantic Kernel, Vercel AI SDK (4) ---
    {
        "url": "https://ai.pydantic.dev/",
        "title": "PydanticAI — Python Agent Framework with Type Safety",
        "description": "Official documentation for PydanticAI, the Python agent framework bringing FastAPI-style ergonomics to GenAI development with structured output validation, dependency injection, and model-agnostic support across OpenAI, Anthropic, Gemini, and other providers.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://dspy.ai/",
        "title": "DSPy — Declarative Self-Improving Language Model Programming",
        "description": "Official documentation for DSPy from Stanford NLP, a framework for programming rather than prompting language models using composable modules (Predict, ChainOfThought, ReAct) with automatic prompt optimization.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://learn.microsoft.com/en-us/semantic-kernel/overview/",
        "title": "Semantic Kernel — Microsoft's AI Orchestration SDK",
        "description": "Official Microsoft documentation for Semantic Kernel, a lightweight open-source AI orchestration SDK for C#, Python, and Java that serves as middleware for integrating LLMs into enterprise applications with agent orchestration patterns.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://ai-sdk.dev/docs/introduction",
        "title": "Vercel AI SDK — Unified TypeScript Interface for LLM Streaming",
        "description": "Official documentation for the Vercel AI SDK providing a unified TypeScript interface for streaming AI responses across OpenAI, Anthropic, Gemini, and other providers with generateText and streamText primitives for building AI-powered web applications.",
        "is_positive": True,
        "label": "positive",
    },
    # --- Production RAG & Hybrid Search (3) ---
    {
        "url": "https://jkatz05.com/post/postgres/hybrid-search-postgres-pgvector/",
        "title": "Hybrid Search with PostgreSQL and pgvector — Jonathan Katz",
        "description": "Authoritative guide by a PostgreSQL core contributor on implementing hybrid search combining BM25 keyword precision with pgvector semantic understanding using Reciprocal Rank Fusion, entirely within PostgreSQL.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://www.pinecone.io/learn/series/rag/rerankers/",
        "title": "Rerankers and Two-Stage Retrieval — Pinecone",
        "description": "Authoritative guide on two-stage retrieval with cross-encoder rerankers covering bi-encoder vs cross-encoder tradeoffs, NDCG improvements, and production implementation patterns for RAG pipelines.",
        "is_positive": True,
        "label": "positive",
    },
    # --- Prompt Engineering & CLAUDE.md (2) ---
    {
        "url": "https://simonwillison.net/tags/prompt-engineering/",
        "title": "Simon Willison on Prompt Engineering — Practical Techniques",
        "description": "Simon Willison's collected writings on prompt engineering covering practical techniques for code generation, structured output extraction, and defensive prompting patterns tested against real-world LLM deployments.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/",
        "title": "Developer's Guide to Multi-Agent Patterns — Google ADK",
        "description": "Google's guide to multi-agent design patterns covering sequential, parallel, and hierarchical orchestration with deterministic workflow engines and specialized critic agents for production agent systems.",
        "is_positive": True,
        "label": "positive",
    },
    # --- LLM API Docs: OpenAI, Gemini, Mistral (3) ---
    {
        "url": "https://platform.openai.com/docs/changelog",
        "title": "OpenAI API Changelog — Breaking Changes and New Features",
        "description": "Official OpenAI API changelog tracking model releases, API feature additions, deprecations, and breaking changes essential for monitoring the OpenAI developer ecosystem.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://ai.google.dev/gemini-api/docs",
        "title": "Gemini API Documentation — Google AI for Developers",
        "description": "Official Gemini API documentation covering text generation, multimodal inputs, function calling, grounding, and the Gemini model family with agentic capabilities for building AI-powered applications.",
        "is_positive": True,
        "label": "positive",
    },
    {
        "url": "https://docs.mistral.ai/",
        "title": "Mistral AI Documentation — Models, API, and Agents",
        "description": "Official Mistral AI documentation covering their model lineup, Chat Completions API, function calling, and the Agents API with built-in code execution and MCP tool support for building AI applications.",
        "is_positive": True,
        "label": "positive",
    },
    # -------------------------------------------------------------------------
    # NEGATIVE ITEMS: Generic tech content unrelated to Claude Code / MCP
    # -------------------------------------------------------------------------
    # --- Generic React / Vue.js (5) ---
    {
        "url": "https://react.dev/learn",
        "title": "React Getting Started — Frontend framework tutorial",
        "description": "Official React tutorial covering components, state, effects, and modern React patterns.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://vuejs.org/guide/introduction.html",
        "title": "Vue.js Introduction — Progressive JavaScript framework",
        "description": "Introduction to Vue.js, the progressive JavaScript framework for building user interfaces.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://nextjs.org/docs",
        "title": "Next.js Documentation — React meta-framework",
        "description": "Official documentation for Next.js, the React framework for production with SSR and SSG.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://angular.dev/overview",
        "title": "Angular Overview — Google's web framework",
        "description": "Overview of Angular, Google's TypeScript-based framework for building web applications.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://svelte.dev/docs/introduction",
        "title": "Svelte Introduction — Compiler-based UI framework",
        "description": "Introduction to Svelte, a compiler-based approach to building reactive user interfaces.",
        "is_positive": False,
        "label": "negative",
    },
    # --- Unrelated LLM News (10) ---
    {
        "url": "https://openai.com/index/gpt-4o",
        "title": "GPT-4o Announcement — OpenAI multimodal model",
        "description": "OpenAI announces GPT-4o, a natively multimodal model handling text, vision, and audio.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://deepmind.google/technologies/gemini/",
        "title": "Gemini — Google's multimodal AI",
        "description": "Google DeepMind's Gemini family of multimodal AI models for text, images, audio, and video.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://ai.meta.com/blog/llama-3/",
        "title": "Llama 3 Release — Meta open source LLM",
        "description": "Meta releases Llama 3, an open source large language model family for research and commercial use.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://mistral.ai/news/mistral-large",
        "title": "Mistral Large — European AI model",
        "description": "Mistral AI announces Mistral Large, a high-capability LLM competing with GPT-4 class models.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://huggingface.co/docs/transformers/",
        "title": "HuggingFace Transformers — ML library documentation",
        "description": "Documentation for HuggingFace Transformers library for state-of-the-art NLP and ML models.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://openai.com/chatgpt/",
        "title": "ChatGPT — Consumer AI chat product",
        "description": "OpenAI's consumer AI chat interface powered by GPT models for general-purpose conversations.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://github.com/ggerganov/llama.cpp",
        "title": "llama.cpp — CPU inference for LLMs",
        "description": "Open source library for running large language models efficiently on CPU using C/C++.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://lmsys.org/",
        "title": "LMSYS Chatbot Arena — LLM benchmarking platform",
        "description": "Platform for evaluating and benchmarking language models through crowdsourced human preferences.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://github.com/mlc-ai/mlc-llm",
        "title": "MLC LLM — Universal deployment for LLMs",
        "description": "Framework for compiling and deploying LLMs across diverse hardware backends including mobile.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://github.com/ollama/ollama",
        "title": "Ollama — Local LLM runner",
        "description": "Tool for running open-source large language models locally on your computer with a simple CLI.",
        "is_positive": False,
        "label": "negative",
    },
    # --- Generic Python Libraries (5) ---
    {
        "url": "https://pandas.pydata.org/docs/",
        "title": "Pandas Documentation — Data analysis library",
        "description": "Official documentation for Pandas, the Python data analysis and manipulation library.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://docs.python-requests.org/",
        "title": "Requests Library — HTTP for humans",
        "description": "Documentation for the Requests library, the de facto standard for HTTP in Python.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://matplotlib.org/stable/tutorials/",
        "title": "Matplotlib Tutorials — Data visualization",
        "description": "Tutorials for Matplotlib, Python's comprehensive library for creating static and interactive visualizations.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://flask.palletsprojects.com/",
        "title": "Flask Documentation — Python micro web framework",
        "description": "Official Flask documentation for building web applications with the lightweight Python framework.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://www.djangoproject.com/start/",
        "title": "Django Getting Started — Full-stack Python framework",
        "description": "Getting started guide for Django, the batteries-included Python web framework for rapid development.",
        "is_positive": False,
        "label": "negative",
    },
    # --- Crypto / Web3 (5) ---
    {
        "url": "https://ethereum.org/en/developers/docs/",
        "title": "Ethereum Developer Docs — Blockchain development",
        "description": "Comprehensive developer documentation for building on the Ethereum blockchain.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://solana.com/docs",
        "title": "Solana Documentation — High-performance blockchain",
        "description": "Official Solana documentation for developing dApps on the high-throughput Solana blockchain.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://docs.opensea.io/",
        "title": "OpenSea API — NFT marketplace",
        "description": "API documentation for OpenSea, the largest NFT marketplace for buying and selling digital assets.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://docs.uniswap.org/",
        "title": "Uniswap Docs — Decentralized exchange protocol",
        "description": "Documentation for Uniswap, the leading decentralized exchange protocol on Ethereum.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://bitcoin.org/en/developer-guide",
        "title": "Bitcoin Developer Guide — Cryptocurrency protocol",
        "description": "Technical developer guide to the Bitcoin protocol, transactions, and blockchain mechanics.",
        "is_positive": False,
        "label": "negative",
    },
    # --- General DevOps (5) ---
    {
        "url": "https://docs.docker.com/get-started/",
        "title": "Docker Getting Started — Containerization basics",
        "description": "Official Docker getting started guide covering containers, images, and Docker Compose basics.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://kubernetes.io/docs/tutorials/",
        "title": "Kubernetes Tutorials — Container orchestration",
        "description": "Official Kubernetes tutorials for deploying and managing containerized applications at scale.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://www.terraform.io/docs",
        "title": "Terraform Documentation — Infrastructure as code",
        "description": "Documentation for Terraform, HashiCorp's infrastructure as code tool for cloud provisioning.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://docs.ansible.com/",
        "title": "Ansible Documentation — IT automation",
        "description": "Official Ansible documentation for IT automation, configuration management, and orchestration.",
        "is_positive": False,
        "label": "negative",
    },
    {
        "url": "https://prometheus.io/docs/introduction/",
        "title": "Prometheus — Monitoring and alerting",
        "description": "Introduction to Prometheus, the open-source systems monitoring and alerting toolkit.",
        "is_positive": False,
        "label": "negative",
    },
]

# Voyage-safe batch size (stay well under rate limits)
EMBED_BATCH_SIZE = 20


async def main() -> None:
    await init_db()
    settings = get_settings()
    redis_client = aioredis.from_url(settings.REDIS_URL)
    spend_tracker = SpendTracker(redis_client)
    llm_client = LLMClient(spend_tracker)

    async with _db.async_session_factory() as session:
        # 1. Find which items are already seeded (idempotent check)
        existing = await session.execute(select(ReferenceItem.url))
        existing_urls = {row[0] for row in existing.fetchall()}

        new_items = [
            item for item in REFERENCE_ITEMS if item["url"] not in existing_urls
        ]
        if not new_items:
            print(
                f"All {len(REFERENCE_ITEMS)} reference items already seeded. Nothing to do."
            )
            await redis_client.aclose()
            return

        print(
            f"Seeding {len(new_items)} new reference items "
            f"({len(existing_urls)} already exist)..."
        )

        # 2. Embed in batches and store
        for i in range(0, len(new_items), EMBED_BATCH_SIZE):
            batch = new_items[i : i + EMBED_BATCH_SIZE]
            texts = [
                build_embed_input(item["title"], item["description"] or "")
                for item in batch
            ]
            embeddings = await llm_client.get_embeddings(texts)

            for item_data, embedding in zip(batch, embeddings):
                ref = ReferenceItem(
                    url=item_data["url"],
                    title=item_data["title"],
                    description=item_data["description"],
                    embedding=embedding,
                    embedding_model_version=settings.EMBEDDING_MODEL,
                    label=item_data["label"],
                    is_positive=item_data["is_positive"],
                )
                session.add(ref)

            await session.commit()
            print(f"  Embedded and stored batch {i // EMBED_BATCH_SIZE + 1}")

        # 3. Verify all items have embeddings (assertion on correctness)
        null_check = await session.execute(
            text("SELECT COUNT(*) FROM reference_items WHERE embedding IS NULL")
        )
        null_count = null_check.scalar()
        total_check = await session.execute(
            text("SELECT COUNT(*) FROM reference_items")
        )
        total = total_check.scalar()

        print(f"Done. Total: {total} reference items. Null embeddings: {null_count}")
        assert null_count == 0, f"FATAL: {null_count} items have NULL embeddings!"

    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
