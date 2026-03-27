# Intel Overdrive

[![npm version](https://img.shields.io/npm/v/intel-overdrive.svg)](https://www.npmjs.com/package/intel-overdrive)
[![npm downloads](https://img.shields.io/npm/dm/intel-overdrive)](https://www.npmjs.com/package/intel-overdrive)
[![GitHub stars](https://img.shields.io/github/stars/Looney-tic/intel-overdrive)](https://github.com/Looney-tic/intel-overdrive)

Your AI coding agent doesn't know what shipped last week. This gives it a live feed of breaking changes, new tools, and security alerts from 1,100+ sources — so it stops generating wrong code.

## Get started

```
npx intel-overdrive setup
```

No email, no account, no restart. Works immediately.

| Instead of...                        | Ask your agent                              |
| ------------------------------------ | ------------------------------------------- |
| Scrolling Twitter for AI news        | "What's new in AI coding this week?"        |
| Checking changelogs before upgrading | "Any breaking changes I should know about?" |
| Googling "best tool for X"           | "What's the best MCP server for databases?" |

> [!TIP]
> Also via [skills.sh](https://skills.sh/Looney-tic/agent-skills): `npx skills add Looney-tic/agent-skills --skill intel-overdrive -g -y`

## CLI reference

| Command                                     | Description                              |
| ------------------------------------------- | ---------------------------------------- |
| `intel-overdrive setup`                     | Register API key, install CLI, add skill |
| `intel-overdrive search "query"`            | Find tools, docs, best practices         |
| `intel-overdrive feed [--days N] [--tag T]` | Recent updates sorted by significance    |
| `intel-overdrive breaking [--days N]`       | Breaking changes and deprecations        |
| `intel-overdrive briefing [--days N]`       | Synthesized intelligence briefing        |
| `intel-overdrive library "query"`           | Best practices and guides                |
| `intel-overdrive similar "concept"`         | Semantically similar items               |
| `intel-overdrive action-items`              | Security alerts, urgent items            |
| `intel-overdrive status`                    | Pipeline health                          |

## Links

- [Website](https://inteloverdrive.com)
- [Skills on skills.sh](https://skills.sh/Looney-tic/agent-skills)
- [GitHub](https://github.com/Looney-tic/intel-overdrive)
- [API docs](https://inteloverdrive.com/v1/guide)
