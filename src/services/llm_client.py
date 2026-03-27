from anthropic import (
    AsyncAnthropic,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
)
import voyageai
import asyncio
from dataclasses import dataclass, field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from ..core.config import get_settings
from ..core.logger import get_logger
from .spend_tracker import SpendTracker, SpendLimitExceeded
from typing import List, Optional
import json

logger = get_logger(__name__)


class APICreditsExhausted(Exception):
    """Raised when an API returns 402/payment-required or billing error."""

    def __init__(self, provider: str, detail: str):
        self.provider = provider
        self.detail = detail
        super().__init__(f"{provider} credits exhausted: {detail}")


# Haiku 4.5 pricing (per 1M tokens)
# Batch API is 50% cheaper than real-time
HAIKU_INPUT_PRICE_PER_M = 1.00
HAIKU_OUTPUT_PRICE_PER_M = 5.00
HAIKU_BATCH_INPUT_PRICE_PER_M = 0.50
HAIKU_BATCH_OUTPUT_PRICE_PER_M = 2.50

# Voyage 3.5 lite pricing (per 1M tokens)
VOYAGE_PRICE_PER_M = 0.02
# Average token count used for embedding cost estimation
VOYAGE_ESTIMATED_TOKENS_PER_DOC = 500


@dataclass
class LLMResponse:
    primary_type: str
    tags: list[str]
    confidence: float
    raw_text: str
    input_tokens: int
    output_tokens: int
    cost: float
    summary: str = ""
    significance: str = "informational"


class LLMClient:
    def __init__(self, spend_tracker: SpendTracker):
        self.settings = get_settings()
        self.spend_tracker = spend_tracker

        # Initialize clients
        self.anthropic = AsyncAnthropic(api_key=self.settings.ANTHROPIC_API_KEY)
        self.voyage = voyageai.AsyncClient(
            api_key=self.settings.VOYAGE_API_KEY, timeout=30.0
        )

    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Returns embeddings for the given texts. Auto-chunks into Voyage's
        128-text-per-call limit for large batches.

        Pre-flight spend check is performed before the first API call.
        Actual cost is tracked post-call using estimated token counts.
        """
        VOYAGE_MAX_BATCH = 128

        # Pre-flight gate check (does not increment)
        await self.spend_tracker.check_spend_gate()

        all_embeddings: List[List[float]] = []

        @retry(
            retry=retry_if_exception_type((TimeoutError, ConnectionError, OSError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=1, max=30),
            reraise=True,
        )
        async def _embed_chunk(chunk):
            return await self.voyage.embed(
                chunk,
                model=self.settings.EMBEDDING_MODEL,
                input_type="document",
            )

        for i in range(0, len(texts), VOYAGE_MAX_BATCH):
            chunk = texts[i : i + VOYAGE_MAX_BATCH]
            try:
                response = await _embed_chunk(chunk)
            except Exception as exc:
                err_str = str(exc).lower()
                if any(
                    k in err_str
                    for k in (
                        "402",
                        "payment",
                        "billing",
                        "credit",
                        "quota",
                        "insufficient",
                    )
                ):
                    logger.error("VOYAGE_CREDITS_EXHAUSTED", error=str(exc))
                    raise APICreditsExhausted("voyage", str(exc))
                raise
            all_embeddings.extend(response.embeddings)

        # Voyage doesn't return token counts; use estimate for cost tracking
        estimated_tokens = len(texts) * VOYAGE_ESTIMATED_TOKENS_PER_DOC
        actual_cost = estimated_tokens * (VOYAGE_PRICE_PER_M / 1_000_000)

        try:
            await self.spend_tracker.track_spend(actual_cost)
        except SpendLimitExceeded:
            logger.warning(
                "SPEND_LIMIT_HIT_AFTER_EMBEDDING",
                estimated_cost=actual_cost,
                texts_count=len(texts),
            )

        return all_embeddings

    async def classify(self, content: str, system_prompt: str) -> LLMResponse:
        """
        Classifies content using the configured LLM model.

        Pre-flight spend check is performed before the API call.
        Actual cost is tracked from real token counts post-call.
        Returns structured LLMResponse.
        """
        # Pre-flight gate check (does not increment)
        await self.spend_tracker.check_spend_gate()

        try:
            response = await self.anthropic.messages.create(
                model=self.settings.LLM_MODEL,
                max_tokens=1024,
                temperature=0.1,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": content}],
            )
        except APIStatusError as exc:
            if exc.status_code in (402, 403, 429):
                err_detail = str(exc.message) if hasattr(exc, "message") else str(exc)
                if exc.status_code == 402 or any(
                    k in err_detail.lower()
                    for k in ("billing", "credit", "payment", "quota", "insufficient")
                ):
                    logger.error(
                        "ANTHROPIC_CREDITS_EXHAUSTED",
                        status=exc.status_code,
                        error=err_detail,
                    )
                    raise APICreditsExhausted("anthropic", err_detail)
            raise

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        actual_cost = input_tokens * (
            HAIKU_INPUT_PRICE_PER_M / 1_000_000
        ) + output_tokens * (HAIKU_OUTPUT_PRICE_PER_M / 1_000_000)

        # Track actual cost post-call
        try:
            await self.spend_tracker.track_spend(actual_cost)
        except SpendLimitExceeded:
            # Limit hit after the API call — result is valid, log and continue
            logger.warning(
                "SPEND_LIMIT_HIT_AFTER_CLASSIFY",
                actual_cost=actual_cost,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        raw_text = response.content[0].text

        # Strip markdown code fences if present (Haiku wraps JSON in ```json ... ```)
        text_to_parse = raw_text.strip()
        if text_to_parse.startswith("```"):
            lines = text_to_parse.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text_to_parse = "\n".join(lines).strip()

        try:
            parsed = json.loads(text_to_parse)
            primary_type = parsed.get("primary_type", "unknown")
            tags = parsed.get("tags", [])
            confidence = float(parsed.get("confidence", 0.0))
            summary = parsed.get("summary", "")
            significance = parsed.get("significance", "informational")
        except Exception as e:
            logger.error("LLM_PARSE_ERROR", error=str(e), content=raw_text)
            primary_type = "unknown"
            tags = []
            confidence = 0.0
            summary = ""
            significance = "informational"

        return LLMResponse(
            primary_type=primary_type,
            tags=tags,
            confidence=confidence,
            summary=summary,
            significance=significance,
            raw_text=raw_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=actual_cost,
        )

    async def classify_batch(
        self,
        items: list[dict],
        system_prompt: str,
        redis_client=None,
        poll_interval: int = 10,
        max_wait: int = 1500,
    ) -> dict[str, LLMResponse]:
        """Classify multiple items using the Anthropic Message Batches API (50% cheaper).

        Args:
            items: list of {"custom_id": str, "content": str}
            system_prompt: system prompt for classification
            redis_client: optional Redis client for batch-ID persistence (prevents recovery race)
            poll_interval: seconds between status polls
            max_wait: maximum seconds to wait for batch completion

        Returns:
            dict mapping custom_id → LLMResponse
        """
        if not items:
            return {}

        # Pre-flight spend check
        await self.spend_tracker.check_spend_gate()

        # Build batch requests
        requests = [
            {
                "custom_id": item["custom_id"],
                "params": {
                    "model": self.settings.LLM_MODEL,
                    "max_tokens": 1024,
                    "temperature": 0.1,
                    "system": [
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral", "ttl": "1h"},
                        }
                    ],
                    "messages": [{"role": "user", "content": item["content"]}],
                },
            }
            for item in items
        ]

        # Submit batch (retry on transient network/server errors)
        @retry(
            retry=retry_if_exception_type(
                (APIConnectionError, APITimeoutError, InternalServerError)
            ),
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=2, max=30),
            reraise=True,
        )
        async def _submit_batch():
            return await self.anthropic.messages.batches.create(requests=requests)

        batch = await _submit_batch()
        logger.info("BATCH_SUBMITTED", batch_id=batch.id, count=len(requests))

        # Store batch ID in Redis so recovery reaper won't reset items while we poll.
        # TTL = 30 minutes (generous headroom over max_wait default of 25 min).
        if redis_client is not None:
            await redis_client.set("batch:active:classify", batch.id, ex=1800)

        # Poll until complete
        waited = 0
        batch_status = None
        while waited < max_wait:
            try:
                batch_status = await self.anthropic.messages.batches.retrieve(batch.id)
            except Exception as exc:
                logger.warning("BATCH_POLL_ERROR", batch_id=batch.id, error=str(exc))
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                continue
            if batch_status.processing_status == "ended":
                break
            await asyncio.sleep(poll_interval)
            waited += poll_interval

        if batch_status is None or batch_status.processing_status != "ended":
            logger.error("BATCH_TIMEOUT", batch_id=batch.id, waited=waited)
            # Do NOT delete Redis key on timeout — let next invocation resume polling.
            return {}

        # Collect results
        results: dict[str, LLMResponse] = {}
        total_cost = 0.0

        results_iter = await self.anthropic.messages.batches.results(batch.id)
        async for result in results_iter:
            custom_id = result.custom_id
            if result.result.type != "succeeded":
                error_detail = ""
                if hasattr(result.result, "error") and result.result.error:
                    error_detail = str(result.result.error)
                elif hasattr(result.result, "message"):
                    error_detail = str(result.result.message)
                logger.warning(
                    "BATCH_ITEM_FAILED",
                    custom_id=custom_id,
                    type=result.result.type,
                    error=error_detail,
                )
                continue

            msg = result.result.message
            input_tokens = msg.usage.input_tokens
            output_tokens = msg.usage.output_tokens
            # Batch API pricing: 50% of real-time
            item_cost = input_tokens * (
                HAIKU_BATCH_INPUT_PRICE_PER_M / 1_000_000
            ) + output_tokens * (HAIKU_BATCH_OUTPUT_PRICE_PER_M / 1_000_000)
            total_cost += item_cost

            raw_text = msg.content[0].text

            # Strip markdown code fences
            text_to_parse = raw_text.strip()
            if text_to_parse.startswith("```"):
                lines = text_to_parse.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text_to_parse = "\n".join(lines).strip()

            try:
                try:
                    parsed = json.loads(text_to_parse)
                except json.JSONDecodeError as json_err:
                    # "Extra data" means LLM appended text after the JSON object.
                    # raw_decode extracts just the first valid JSON object.
                    if "Extra data" in str(json_err):
                        parsed, _ = json.JSONDecoder().raw_decode(text_to_parse)
                    else:
                        raise
                primary_type = parsed.get("primary_type", "unknown")
                tags = parsed.get("tags", [])
                confidence = float(parsed.get("confidence", 0.0))
                summary = parsed.get("summary", "")
                significance = parsed.get("significance", "informational")
            except Exception as e:
                logger.error(
                    "BATCH_PARSE_ERROR",
                    custom_id=custom_id,
                    error=str(e),
                    raw_snippet=text_to_parse[:200],
                )
                primary_type = "unknown"
                tags = []
                confidence = 0.0
                summary = ""
                significance = "informational"

            results[custom_id] = LLMResponse(
                primary_type=primary_type,
                tags=tags,
                confidence=confidence,
                summary=summary,
                significance=significance,
                raw_text=raw_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=item_cost,
            )

        # Track total batch cost
        try:
            await self.spend_tracker.track_spend(total_cost)
        except SpendLimitExceeded:
            logger.warning(
                "SPEND_LIMIT_HIT_AFTER_BATCH", total_cost=total_cost, count=len(results)
            )

        # Batch completed successfully — clear the Redis active-batch key
        if redis_client is not None:
            await redis_client.delete("batch:active:classify")

        logger.info(
            "BATCH_COMPLETE",
            batch_id=batch.id,
            classified=len(results),
            total_cost=round(total_cost, 6),
        )

        return results

    async def poll_existing_batch(
        self,
        batch_id: str,
        redis_client=None,
        poll_interval: int = 10,
        max_wait: int = 1500,
    ) -> dict[str, LLMResponse]:
        """Resume polling an already-submitted batch by ID.

        Called when the recovery reaper finds an active batch in Redis instead of
        creating a new one. Harvests results if the batch has ended, or continues
        polling if still in progress.

        Returns:
            dict mapping custom_id → LLMResponse (empty dict on timeout or error)
        """
        logger.info("BATCH_RESUME_POLLING", batch_id=batch_id)

        waited = 0
        batch_status = None
        while waited < max_wait:
            try:
                batch_status = await self.anthropic.messages.batches.retrieve(batch_id)
            except Exception as exc:
                logger.warning(
                    "BATCH_RESUME_POLL_ERROR", batch_id=batch_id, error=str(exc)
                )
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                continue
            if batch_status.processing_status == "ended":
                break
            await asyncio.sleep(poll_interval)
            waited += poll_interval

        if batch_status is None or batch_status.processing_status != "ended":
            logger.error("BATCH_RESUME_TIMEOUT", batch_id=batch_id, waited=waited)
            return {}

        # Collect results (same parsing logic as classify_batch)
        results: dict[str, LLMResponse] = {}
        total_cost = 0.0

        results_iter = await self.anthropic.messages.batches.results(batch_id)
        async for result in results_iter:
            custom_id = result.custom_id
            if result.result.type != "succeeded":
                error_detail = ""
                if hasattr(result.result, "error") and result.result.error:
                    error_detail = str(result.result.error)
                elif hasattr(result.result, "message"):
                    error_detail = str(result.result.message)
                logger.warning(
                    "BATCH_ITEM_FAILED",
                    custom_id=custom_id,
                    type=result.result.type,
                    error=error_detail,
                )
                continue

            msg = result.result.message
            input_tokens = msg.usage.input_tokens
            output_tokens = msg.usage.output_tokens
            item_cost = input_tokens * (
                HAIKU_BATCH_INPUT_PRICE_PER_M / 1_000_000
            ) + output_tokens * (HAIKU_BATCH_OUTPUT_PRICE_PER_M / 1_000_000)
            total_cost += item_cost

            raw_text = msg.content[0].text

            text_to_parse = raw_text.strip()
            if text_to_parse.startswith("```"):
                lines = text_to_parse.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text_to_parse = "\n".join(lines).strip()

            try:
                try:
                    parsed = json.loads(text_to_parse)
                except json.JSONDecodeError as json_err:
                    # "Extra data" means LLM appended text after the JSON object.
                    # raw_decode extracts just the first valid JSON object.
                    if "Extra data" in str(json_err):
                        parsed, _ = json.JSONDecoder().raw_decode(text_to_parse)
                    else:
                        raise
                primary_type = parsed.get("primary_type", "unknown")
                tags = parsed.get("tags", [])
                confidence = float(parsed.get("confidence", 0.0))
                summary = parsed.get("summary", "")
                significance = parsed.get("significance", "informational")
            except Exception as e:
                logger.error(
                    "BATCH_PARSE_ERROR",
                    custom_id=custom_id,
                    error=str(e),
                    raw_snippet=text_to_parse[:200],
                )
                primary_type = "unknown"
                tags = []
                confidence = 0.0
                summary = ""
                significance = "informational"

            results[custom_id] = LLMResponse(
                primary_type=primary_type,
                tags=tags,
                confidence=confidence,
                summary=summary,
                significance=significance,
                raw_text=raw_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=item_cost,
            )

        try:
            await self.spend_tracker.track_spend(total_cost)
        except SpendLimitExceeded:
            logger.warning(
                "SPEND_LIMIT_HIT_AFTER_BATCH_RESUME",
                total_cost=total_cost,
                count=len(results),
            )

        # Batch completed — clear the Redis active-batch key
        if redis_client is not None:
            await redis_client.delete("batch:active:classify")

        logger.info(
            "BATCH_RESUME_COMPLETE",
            batch_id=batch_id,
            classified=len(results),
            total_cost=round(total_cost, 6),
        )

        return results
