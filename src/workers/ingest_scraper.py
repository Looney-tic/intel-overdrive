"""Playwright web scraper adapter: cron dispatcher + per-source job.

Handles JS-rendered sites (OpenAI changelog, Cursor blog, Mistral blog, etc.)
that return blank HTML to raw HTTP GET. CSS selectors are defined per-source
in Source.config["selectors"].

Special post-processing modes:
- "github_trending": Extracts individual repos from GitHub Trending page with
  stars, language, and per-repo IntelItems (instead of one HTML blob).
"""

import re
import urllib.parse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from sqlalchemy import select

import src.core.init_db as _db
from src.core.logger import get_logger
from src.workers.content_fetcher import fetch_article_body
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)
from src.workers.dms_worker import update_ingestion_heartbeat

logger = get_logger(__name__)


async def poll_scraper_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active scraper sources and enqueues per-source jobs.

    Jobs are staggered with _defer_by so they arrive in pairs matching the
    semaphore capacity (SCRAPER_SEMAPHORE_MAX=2). Each pair is spaced 90s apart
    to allow Chromium to finish (~30-45s per scrape + buffer).
    """
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "scraper",
            )
        )
        sources = result.scalars().all()

    # Stagger: 2 jobs every 90 seconds (matching semaphore max of 2)
    for i, source in enumerate(sources):
        delay_seconds = (i // SCRAPER_SEMAPHORE_MAX) * 90
        await ctx["redis"].enqueue_job(
            "ingest_scraper_source",
            source.id,
            _queue_name="fast",
            _defer_by=delay_seconds,
        )

    if sources:
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info(
        "poll_scraper_sources_dispatched",
        count=len(sources),
        total_window_seconds=(len(sources) // SCRAPER_SEMAPHORE_MAX) * 90,
    )


SCRAPER_SEMAPHORE_KEY = "scraper:semaphore"
SCRAPER_SEMAPHORE_MAX = (
    2  # Max 2 concurrent Chromium instances (~300MB each, 768m container limit)
)
SCRAPER_SEMAPHORE_TTL = 300  # 5-minute TTL to prevent leaked semaphore on crash


async def ingest_scraper_source(ctx: dict, source_id: str) -> None:
    """Per-source job: fetch JS-rendered page, extract articles, deduplicate, and store.

    A Redis semaphore limits concurrent scraper jobs to SCRAPER_SEMAPHORE_MAX (2),
    preventing OOM kills from multiple Chromium instances.
    """
    redis_client = ctx["redis"]

    if _db.async_session_factory is None:
        logger.error("db_not_initialized", source_id=source_id)
        return

    # Redis semaphore: limit concurrent Playwright/Chromium instances to 2.
    # Each Chromium instance uses ~160-300MB; 2 instances stay well under 512MB limit.
    acquired = await redis_client.incr(SCRAPER_SEMAPHORE_KEY)
    # Always refresh TTL to prevent leaked semaphore from crashed workers.
    await redis_client.expire(SCRAPER_SEMAPHORE_KEY, SCRAPER_SEMAPHORE_TTL)
    if acquired > SCRAPER_SEMAPHORE_MAX:
        await redis_client.decr(SCRAPER_SEMAPHORE_KEY)
        # Re-enqueue with 60s delay instead of silently dropping
        logger.info("scraper_semaphore_retry", source_id=source_id, slots=acquired)
        await redis_client.enqueue_job(
            "ingest_scraper_source",
            source_id,
            _queue_name="fast",
            _defer_by=60,
        )
        return

    try:
        await _run_scraper(redis_client, source_id)
    finally:
        await redis_client.decr(SCRAPER_SEMAPHORE_KEY)


async def _extract_trending_repos(
    page, source, session, dedup, seen_urls_set: set, newly_found_urls: list
) -> int:
    """Extract individual repos from GitHub Trending page.

    Instead of storing the entire trending page as one IntelItem, this extracts
    each trending repo as a separate item with title, URL, description, star
    count, and programming language.

    GitHub Trending page structure (each row is an article.Box-row):
      - h2 > a: repo link (href="/owner/repo")
      - p: description text
      - Star count: in a link/span containing star SVG or matching star pattern
      - Language: span[itemprop='programmingLanguage']
    """
    items = await page.query_selector_all("article.Box-row")
    new_count = 0

    for item_el in items:
        # Extract repo URL from h2 > a
        link_el = await item_el.query_selector("h2 a")
        if link_el is None:
            continue
        href = await link_el.get_attribute("href")
        if not href:
            continue

        # Build full GitHub URL
        repo_url = urllib.parse.urljoin("https://github.com", href.strip())

        # Extract owner/repo from URL path
        path_parts = href.strip("/").split("/")
        if len(path_parts) < 2:
            continue
        owner_repo = f"{path_parts[0]}/{path_parts[1]}"

        # Extract description
        desc_el = await item_el.query_selector("p")
        description = ""
        if desc_el is not None:
            description = (await desc_el.inner_text()).strip()

        # Extract star count — look for the link that has an SVG star icon,
        # or try the overall text content with a digit pattern
        stars_text = ""
        # Try multiple selectors for star count (GitHub Trending structure varies)
        for star_selector in [
            "a.Link--muted.d-inline-block.mr-3",
            "a[href$='/stargazers']",
            "span.d-inline-block.float-sm-right",
        ]:
            star_el = await item_el.query_selector(star_selector)
            if star_el is not None:
                raw = (await star_el.inner_text()).strip()
                # Clean: remove commas and whitespace, look for digits
                cleaned = raw.replace(",", "").replace(" ", "")
                if re.search(r"\d+", cleaned):
                    stars_text = raw.strip()
                    break

        # Extract programming language
        lang = ""
        lang_el = await item_el.query_selector("span[itemprop='programmingLanguage']")
        if lang_el is not None:
            lang = (await lang_el.inner_text()).strip()

        # Build content string with metadata
        content_parts = []
        if description:
            content_parts.append(description)
        if stars_text:
            content_parts.append(f"Stars: {stars_text}.")
        if lang:
            content_parts.append(f"Language: {lang}.")
        content_text = " ".join(content_parts) if content_parts else owner_repo

        # Build tags
        tags = ["github", "trending"]
        if lang:
            tags.append(lang.lower())

        # Skip if already seen
        if repo_url in seen_urls_set:
            continue

        newly_found_urls.append(repo_url)

        # DB-level dedup
        if await dedup.check_url_exists(repo_url):
            continue

        # Content fingerprint dedup
        if content_text:
            existing = await dedup.find_duplicate_by_content(content_text)
            if existing:
                logger.info(
                    "DEDUP_CONTENT_SKIP",
                    url=repo_url,
                    existing_id=str(existing.id),
                )
                continue

        url_hash = dedup._compute_url_hash(repo_url)
        content_hash = dedup._get_content_fingerprint(content_text)

        intel_item = IntelItem(
            source_id=source.id,
            external_id=repo_url,
            url=repo_url,
            url_hash=url_hash,
            title=owner_repo,
            content=content_text,
            excerpt=description[:500] if description else None,
            primary_type="unknown",
            tags=tags,
            status="raw",
            content_hash=content_hash,
            source_name=source.name,
        )
        session.add(intel_item)
        new_count += 1

    logger.info(
        "extract_trending_repos_complete",
        source_id=source.id,
        total_rows=len(items),
        new_items=new_count,
    )
    return new_count


async def _run_scraper(redis_client, source_id: str) -> None:
    """Core scraper logic, called after semaphore acquisition."""
    async with _db.async_session_factory() as session:
        result = await session.execute(select(Source).where(Source.id == source_id))
        source = result.scalar_one_or_none()

        if source is None or not source.is_active:
            logger.info(
                "source_skipped", source_id=source_id, reason="not_found_or_inactive"
            )
            return

        if await is_source_on_cooldown(
            redis_client, source_id, source.poll_interval_seconds
        ):
            logger.info("source_on_cooldown", source_id=source_id)
            return

        # Read CSS selectors from source config
        selectors: dict = source.config.get("selectors", {})
        if (
            not selectors.get("item")
            or not selectors.get("title")
            or not selectors.get("url")
        ):
            logger.warning(
                "scraper_missing_required_selectors",
                source_id=source_id,
                selectors=selectors,
            )
            return

        seen_urls: list = source.config.get("seen_urls", [])
        seen_urls_set: set = set(seen_urls)
        wait_for_selector: str | None = source.config.get("wait_for_selector")

        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(
                    user_agent="Overdrive-Intel/1.0 (web scraper)"
                )

                # Block images, CSS, and fonts to reduce bandwidth (~60% savings)
                await page.route(
                    "**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf,eot}",
                    lambda route: route.abort(),
                )

                await page.goto(source.url, timeout=30000)

                # Wait for JS-rendered content to appear
                if wait_for_selector:
                    try:
                        await page.wait_for_selector(wait_for_selector, timeout=15000)
                    except PlaywrightTimeoutError:
                        logger.warning(
                            "wait_for_selector_timeout",
                            source_id=source_id,
                            selector=wait_for_selector,
                        )
                else:
                    try:
                        await page.wait_for_load_state("networkidle", timeout=15000)
                    except PlaywrightTimeoutError:
                        logger.warning(
                            "networkidle_timeout",
                            source_id=source_id,
                        )

                dedup = DedupService(session)
                new_count = 0
                newly_found_urls: list[str] = []

                # Check for post-processing mode (e.g. github_trending)
                post_process = source.config.get("post_process")
                if post_process == "github_trending":
                    new_count = await _extract_trending_repos(
                        page, source, session, dedup, seen_urls_set, newly_found_urls
                    )
                else:
                    # Generic extraction: use configured CSS selectors
                    items = await page.query_selector_all(selectors["item"])

                    for item_el in items:
                        # Extract title
                        title_el = await item_el.query_selector(selectors["title"])
                        if title_el is None:
                            continue
                        title = (await title_el.inner_text()).strip()
                        if not title:
                            continue

                        # Extract URL (href attribute)
                        url_el = await item_el.query_selector(selectors["url"])
                        if url_el is None:
                            continue
                        href = await url_el.get_attribute("href")
                        if not href:
                            continue
                        # Resolve relative URLs against source URL
                        article_url = urllib.parse.urljoin(source.url, href)

                        # Extract optional excerpt
                        excerpt_text: str | None = None
                        excerpt_selector = selectors.get("excerpt", "")
                        if excerpt_selector:
                            excerpt_el = await item_el.query_selector(excerpt_selector)
                            if excerpt_el is not None:
                                excerpt_text = (
                                    await excerpt_el.inner_text()
                                ).strip() or None

                        # Skip URLs seen in prior polls (config-level dedup)
                        if article_url in seen_urls_set:
                            continue

                        # Track this URL regardless of DB dedup
                        newly_found_urls.append(article_url)

                        # DB-level dedup
                        if await dedup.check_url_exists(article_url):
                            continue

                        # Layer 2: content fingerprint dedup
                        content_text = excerpt_text or title

                        # Fetch full article body when scraped content is thin
                        if len(content_text) < 100 and article_url:
                            fetched_body = await fetch_article_body(article_url)
                            if fetched_body:
                                content_text = fetched_body

                        if content_text:
                            existing = await dedup.find_duplicate_by_content(
                                content_text
                            )
                            if existing:
                                logger.info(
                                    "DEDUP_CONTENT_SKIP",
                                    url=article_url,
                                    existing_id=str(existing.id),
                                )
                                continue

                        url_hash = dedup._compute_url_hash(article_url)
                        content_hash = dedup._get_content_fingerprint(content_text)

                        intel_item = IntelItem(
                            source_id=source_id,
                            external_id=article_url,
                            url=article_url,
                            url_hash=url_hash,
                            title=title,
                            content=content_text,
                            excerpt=excerpt_text[:500] if excerpt_text else None,
                            primary_type="unknown",
                            tags=[],
                            status="raw",
                            content_hash=content_hash,
                            source_name=source.name,
                        )
                        session.add(intel_item)
                        new_count += 1

                    if not items:
                        logger.warning(
                            "scraper_no_items_found",
                            source_id=source_id,
                            item_selector=selectors["item"],
                        )

                await browser.close()
                browser = None

                # Update seen_urls: cap at last 100 to bound config size
                # CRITICAL: dict reassignment for SQLAlchemy JSON mutation detection
                # Use dict.fromkeys() instead of set() to preserve insertion order,
                # ensuring the 100 retained URLs are the most recent, not random (M-3).
                if newly_found_urls:
                    new_seen = list(dict.fromkeys(seen_urls + newly_found_urls))[-100:]
                    source.config = {**source.config, "seen_urls": new_seen}

                await session.commit()
                await handle_source_success(session, source)
                logger.info(
                    "ingest_scraper_complete", source_id=source_id, new_items=new_count
                )

        except PlaywrightTimeoutError as exc:
            # Timeout is expected transient — log warning, don't burn circuit breaker
            logger.warning(
                "scraper_timeout",
                source_id=source_id,
                error=str(exc),
            )
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
