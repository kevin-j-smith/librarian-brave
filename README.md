# librarian-brave

A **Brave Search backend** for [librarian](https://github.com/kevin-j-smith/librarian)'s
research loop: Brave's independent web index through its official API, as a
second web source beside SearXNG.

Every query goes to Brave under your account. That is the reason this is a
separate, opt-in package, and why it does nothing until `BRAVE_API_KEY` is
set: without a key a search returns no results, says why, and sends nothing.

## Install

Install **into the same environment as librarian**, then route `web` to it:

```bash
uv pip install librarian-brave
```

```ini
# librarian's .env
BRAVE_API_KEY=...
RESEARCH_SEARCH_ROUTES=web: searxng, brave; video: searxng; science: searxng
```

## Settings

| Setting | Default | What it does |
|---|---|---|
| `BRAVE_API_KEY` | empty | Your Brave Search API key; without it nothing is sent |
| `BRAVE_MONTHLY_QUOTA` | 1000 | Requests a calendar month (UTC), counted locally; a search past it is refused before any request |
| `BRAVE_MIN_INTERVAL_SECONDS` | 1.1 | Seconds between requests (the free plan allows 1 a second) |

The month's count lives in `brave-v1/usage.json` under librarian's search
cache folder. Brave's own rate-limit headers are read too: when a capped
monthly window has nothing left, requests stop until the reset it names. A
window with a limit of 0 has no cap (a usage-billed plan's month reads
`x-ratelimit-limit: 50, 0`), so `BRAVE_MONTHLY_QUOTA` is then the only
monthly bound: set it to what you're willing to spend. A cached
reply costs nothing and isn't counted. Refusals (no key, quota, 401/403,
429) come back to librarian as `degraded`, never as an error that fails the
research task.

It answers `web` only. Brave's documentation, read on September 30, 2026,
didn't confirm a video endpoint, so none is called.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
