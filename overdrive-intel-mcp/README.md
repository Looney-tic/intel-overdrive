# Intel Overdrive

[![npm version](https://img.shields.io/npm/v/intel-overdrive.svg)](https://www.npmjs.com/package/intel-overdrive)
[![Node version](https://img.shields.io/node/v/intel-overdrive.svg)](https://nodejs.org)
[![License](https://img.shields.io/badge/license-ELv2-blue.svg)](LICENSE)

Live AI ecosystem intelligence for your coding agent. Breaking changes, new tools, and security alerts from 1,100+ sources — before they hit training data.

## Get started

```
npx intel-overdrive setup
```

No email, no account, no restart. Works immediately.

> [!TIP]
> Also via [skills.sh](https://skills.sh/Looney-tic/agent-skills): `npx skills add Looney-tic/agent-skills --skill intel-overdrive -g -y`

## How it works

1. **Skill** tells your agent when to query (`~/.claude/skills/`)
2. **CLI** does the querying via Bash — fast, authenticated, no background process
3. **1,100+ sources** monitored continuously

Your agent runs `intel-overdrive search "query"` automatically. Or use the CLI directly:

```bash
intel-overdrive search "MCP servers for auth"
intel-overdrive feed --days 7
intel-overdrive breaking
```

## CLI reference

| Command                               | Description                              |
| ------------------------------------- | ---------------------------------------- |
| `intel-overdrive setup`               | Register API key, install CLI, add skill |
| `intel-overdrive search "query"`      | Search for tools, docs, best practices   |
| `intel-overdrive feed [--days N]`     | Recent updates sorted by significance    |
| `intel-overdrive breaking [--days N]` | Breaking changes and deprecations        |
| `intel-overdrive mcp-enable`          | Optional: register as MCP server         |

## Links

- [Website](https://inteloverdrive.com)
- [Skills on skills.sh](https://skills.sh/Looney-tic/agent-skills)
- [GitHub](https://github.com/Looney-tic/intel-overdrive)
- [API docs](https://inteloverdrive.com/v1/guide)
