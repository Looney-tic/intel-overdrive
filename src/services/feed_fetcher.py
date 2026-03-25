import asyncio
import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

_USER_AGENT = (
    "Overdrive-Intel/1.0 (https://github.com/overdrive-intel; feed aggregator)"
)
_GITHUB_API_BASE = "https://api.github.com"


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def fetch_feed_conditional(
    url: str,
    stored_etag: str | None = None,
    stored_last_modified: str | None = None,
) -> tuple[bytes | None, str | None, str | None]:
    """Fetch a feed URL with conditional GET support.

    Sends If-None-Match / If-Modified-Since headers when stored values are available.
    Retries on transient network errors (TimeoutException, NetworkError) only.

    Returns:
        (content, etag, last_modified) — content is None on 304 Not Modified.
    """
    headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if stored_etag:
        headers["If-None-Match"] = stored_etag
    if stored_last_modified:
        headers["If-Modified-Since"] = stored_last_modified

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    ) as client:
        response = await client.get(url, headers=headers)

        if response.status_code == 304:
            return (None, stored_etag, stored_last_modified)

        response.raise_for_status()
        return (
            response.content,
            response.headers.get("ETag"),
            response.headers.get("Last-Modified"),
        )


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=3, max=60),
    reraise=True,
)
async def fetch_arxiv_feed(
    search_query: str,
    max_results: int = 50,
) -> bytes:
    """Fetch arXiv papers via the Atom API.

    Uses a 60-second timeout (arXiv can be slow for large result sets).
    Retries on transient network errors with a 3-second initial wait per
    arXiv's documented 3-second delay requirement.

    Returns:
        Raw Atom XML bytes for feedparser.
    """
    url = "https://export.arxiv.org/api/query"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=True,
    ) as client:
        response = await client.get(
            url,
            params={
                "search_query": search_query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": max_results,
            },
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        return response.content


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=60),
    reraise=True,
)
async def fetch_github_search(
    query: str,
    github_token: str | None,
    page: int = 1,
    per_page: int = 30,
) -> tuple[dict, dict]:
    """Search GitHub repositories via the GitHub Search API.

    Retries on transient network errors only. Does not retry on 4xx/5xx.

    Returns:
        (response_json, response_headers_dict)
    """
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    params: dict[str, str | int] = {
        "q": query,
        "sort": "updated",
        "per_page": per_page,
        "page": page,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.get(
            f"{_GITHUB_API_BASE}/search/repositories",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return (response.json(), dict(response.headers))


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=60),
    reraise=True,
)
async def fetch_github_repo_info(
    owner: str,
    repo: str,
    github_token: str | None,
) -> dict:
    """Fetch repository metadata via the GitHub REST API.

    Retries on transient network errors only. Does not retry on 4xx/5xx.

    Returns:
        Dict with keys: stargazers_count, forks_count, subscribers_count,
        open_issues_count, description, updated_at, default_branch.
    """
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        response = await client.get(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=60),
    reraise=True,
)
async def fetch_github_file_contents(
    owner: str,
    repo: str,
    path: str,
    github_token: str | None,
) -> dict | None:
    """Fetch a file from a repository via the GitHub Contents API.

    Retries on transient network errors only. Does not retry on 4xx/5xx.

    Returns:
        Dict with keys: sha, content (base64-encoded), encoding — or None on 404.
    Raises:
        httpx.HTTPStatusError: for non-404 HTTP errors.
    """
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        response = await client.get(
            f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
            headers=headers,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=60),
    reraise=True,
)
async def fetch_github_repo_stats(
    owner: str,
    repo: str,
    endpoint: str,
    github_token: str | None,
) -> dict | None:
    """Fetch computed repository statistics via the GitHub REST API.

    ``endpoint`` should be one of: "commit_activity", "contributors",
    "participation".

    GitHub computes stats asynchronously on first request and returns 202
    Accepted while the job is queued. This function retries up to 3 times
    with a 1.5-second delay between retries. If still 202 after all retries,
    returns None — the stats will be ready on the next poll cycle.

    Returns:
        Parsed JSON (dict or list) on 200, None on 202 after retries or 204
        No Content.
    """
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    url = f"{_GITHUB_API_BASE}/repos/{owner}/{repo}/stats/{endpoint}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        for attempt in range(3):
            response = await client.get(url, headers=headers)

            if response.status_code == 204:
                # No content — repository has no data for this stat
                return None

            if response.status_code == 202:
                # GitHub is computing stats in background; wait and retry
                if attempt < 2:
                    await asyncio.sleep(1.5)
                    continue
                # Still 202 after 3 attempts — stats not ready yet
                return None

            response.raise_for_status()
            return response.json()

    return None
