"""
FOUND-06: LLM client tests.

Mocks the Anthropic SDK and Voyage client to test:
- LLMResponse structure
- spend gate integration (called before API)
- SpendLimitExceeded propagation
"""
import pytest
import json
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch, call


@pytest.fixture
def mock_spend_tracker():
    """Mock SpendTracker that allows spending by default."""
    tracker = AsyncMock()
    tracker.check_spend_gate = AsyncMock(return_value=None)  # passes by default
    tracker.track_spend = AsyncMock(return_value=0.001)
    return tracker


@pytest.fixture
def llm_client(mock_spend_tracker):
    """
    LLMClient with mocked Anthropic and Voyage clients.
    Patches at the module level to avoid real API initialization.
    """
    with (
        patch("src.services.llm_client.AsyncAnthropic") as mock_anthropic_cls,
        patch("src.services.llm_client.voyageai.AsyncClient") as mock_voyage_cls,
    ):
        # Set up mock Anthropic client
        mock_anthropic = MagicMock()
        mock_anthropic_cls.return_value = mock_anthropic

        # Set up mock Voyage client
        mock_voyage = MagicMock()
        mock_voyage_cls.return_value = mock_voyage

        from src.services.llm_client import LLMClient

        client = LLMClient(spend_tracker=mock_spend_tracker)

        # Expose the mocks for test inspection
        client._mock_anthropic = mock_anthropic
        client._mock_voyage = mock_voyage

        yield client


def _make_anthropic_response(
    primary_type: str = "tool",
    tags: list = None,
    confidence: float = 0.9,
    input_tokens: int = 100,
    output_tokens: int = 50,
):
    """Build a mock Anthropic messages.create() response."""
    if tags is None:
        tags = ["claude-code", "mcp"]

    payload = json.dumps(
        {
            "primary_type": primary_type,
            "tags": tags,
            "confidence": confidence,
        }
    )

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=payload)]
    mock_response.usage.input_tokens = input_tokens
    mock_response.usage.output_tokens = output_tokens
    return mock_response


@pytest.mark.asyncio
async def test_classify_returns_llm_response_dataclass(llm_client, mock_spend_tracker):
    """FOUND-06: classify() returns LLMResponse with correct fields."""
    from src.services.llm_client import LLMResponse

    mock_response = _make_anthropic_response(
        primary_type="tool", tags=["mcp"], confidence=0.9
    )
    llm_client._mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

    result = await llm_client.classify("Some content", "You are a classifier.")

    assert isinstance(result, LLMResponse)
    assert result.primary_type == "tool"
    assert "mcp" in result.tags
    assert result.confidence == pytest.approx(0.9)
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cost > 0
    assert result.raw_text is not None


@pytest.mark.asyncio
async def test_classify_calls_check_spend_gate_before_api(
    llm_client, mock_spend_tracker
):
    """FOUND-06: classify() calls check_spend_gate before making the Anthropic API call."""
    mock_response = _make_anthropic_response()
    llm_client._mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

    # Track the call order
    call_order = []
    mock_spend_tracker.check_spend_gate.side_effect = lambda: call_order.append("gate")
    original_create = llm_client._mock_anthropic.messages.create

    async def _tracked_create(**kwargs):
        call_order.append("api")
        return mock_response

    llm_client._mock_anthropic.messages.create = _tracked_create

    await llm_client.classify("Content", "System prompt")

    assert call_order[0] == "gate", "check_spend_gate must be called BEFORE the API"
    assert "api" in call_order


@pytest.mark.asyncio
async def test_classify_raises_spend_limit_exceeded_when_gate_blocks(
    llm_client, mock_spend_tracker
):
    """FOUND-06: classify() raises SpendLimitExceeded when check_spend_gate raises."""
    from src.services.spend_tracker import SpendLimitExceeded

    mock_spend_tracker.check_spend_gate.side_effect = SpendLimitExceeded(
        current=10.0, limit=10.0
    )

    with pytest.raises(SpendLimitExceeded):
        await llm_client.classify("Content", "System prompt")

    # Ensure the Anthropic API was NOT called
    llm_client._mock_anthropic.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_classify_handles_malformed_json_gracefully(
    llm_client, mock_spend_tracker
):
    """FOUND-06: classify() returns 'unknown' type when LLM returns non-JSON."""
    bad_response = MagicMock()
    bad_response.content = [MagicMock(text="Not valid JSON at all")]
    bad_response.usage.input_tokens = 50
    bad_response.usage.output_tokens = 10
    llm_client._mock_anthropic.messages.create = AsyncMock(return_value=bad_response)

    result = await llm_client.classify("Content", "System prompt")
    assert result.primary_type == "unknown"
    assert result.tags == []
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_get_embeddings_returns_list_of_float_lists(
    llm_client, mock_spend_tracker
):
    """FOUND-06: get_embeddings() returns list of float lists."""
    # Mock Voyage response
    mock_embed_response = MagicMock()
    mock_embed_response.embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    llm_client._mock_voyage.embed = AsyncMock(return_value=mock_embed_response)

    result = await llm_client.get_embeddings(["text 1", "text 2"])

    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(emb, list) for emb in result)
    assert all(isinstance(v, float) for v in result[0])


@pytest.mark.asyncio
async def test_get_embeddings_calls_check_spend_gate_before_api(
    llm_client, mock_spend_tracker
):
    """FOUND-06: get_embeddings() calls check_spend_gate before Voyage API."""
    call_order = []
    mock_spend_tracker.check_spend_gate.side_effect = lambda: call_order.append("gate")

    mock_embed_response = MagicMock()
    mock_embed_response.embeddings = [[0.1]]

    async def _tracked_embed(texts, **kwargs):
        call_order.append("api")
        return mock_embed_response

    llm_client._mock_voyage.embed = _tracked_embed

    await llm_client.get_embeddings(["text"])

    assert (
        call_order[0] == "gate"
    ), "check_spend_gate must be called BEFORE embedding API"
    assert "api" in call_order


@pytest.mark.asyncio
async def test_get_embeddings_raises_spend_limit_exceeded_when_gate_blocks(
    llm_client, mock_spend_tracker
):
    """FOUND-06: get_embeddings() raises SpendLimitExceeded when check_spend_gate raises."""
    from src.services.spend_tracker import SpendLimitExceeded

    mock_spend_tracker.check_spend_gate.side_effect = SpendLimitExceeded(
        current=10.0, limit=10.0
    )

    with pytest.raises(SpendLimitExceeded):
        await llm_client.get_embeddings(["text"])

    # Voyage API should NOT be called
    llm_client._mock_voyage.embed.assert_not_called()


@pytest.mark.asyncio
async def test_classify_cost_calculated_from_token_counts(
    llm_client, mock_spend_tracker
):
    """FOUND-06: classify() cost is calculated from real token counts (not hardcoded)."""
    mock_response = _make_anthropic_response(input_tokens=1_000_000, output_tokens=0)
    llm_client._mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

    result = await llm_client.classify("Content", "System prompt")

    # 1M input tokens at $1.00/M (Haiku 4.5) = $1.00
    assert result.cost == pytest.approx(1.00, rel=0.01)


@pytest.mark.asyncio
async def test_classify_still_returns_result_when_post_call_limit_hit(
    llm_client, mock_spend_tracker
):
    """FOUND-06: classify() returns result even if spend limit is hit AFTER the API call.

    The pre-flight gate passes, API call succeeds, but track_spend raises because
    the limit was hit by the actual cost. The result should still be returned.
    """
    from src.services.spend_tracker import SpendLimitExceeded

    mock_response = _make_anthropic_response(primary_type="skill", tags=["hooks"])
    llm_client._mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

    # Gate passes, but track_spend raises (post-call limit hit)
    mock_spend_tracker.track_spend.side_effect = SpendLimitExceeded(
        current=10.0, limit=10.0
    )

    result = await llm_client.classify("Content", "System prompt")

    assert result.primary_type == "skill"
    assert "hooks" in result.tags


@pytest.mark.asyncio
async def test_get_embeddings_still_returns_when_post_call_limit_hit(
    llm_client, mock_spend_tracker
):
    """FOUND-06: get_embeddings() returns embeddings even if limit hit after Voyage call.

    Same pattern as classify: pre-flight passes, API succeeds, track_spend raises.
    Embeddings should still be returned.
    """
    from src.services.spend_tracker import SpendLimitExceeded

    mock_embed_response = MagicMock()
    mock_embed_response.embeddings = [[0.1, 0.2, 0.3]]
    llm_client._mock_voyage.embed = AsyncMock(return_value=mock_embed_response)

    mock_spend_tracker.track_spend.side_effect = SpendLimitExceeded(
        current=10.0, limit=10.0
    )

    result = await llm_client.get_embeddings(["text"])

    assert len(result) == 1
    assert result[0] == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_parse_batch_results_raw_decode_fallback(llm_client, mock_spend_tracker):
    """FOUND-26: classify_batch() parses JSON with trailing text via raw_decode fallback.

    When the LLM appends prose after the JSON object (e.g. 'extra text here'),
    json.loads() raises 'Extra data'; raw_decode must extract the valid JSON prefix
    and produce a correct LLMResponse rather than falling back to the unknown/error path.
    """
    # JSON with trailing text — triggers the raw_decode fallback path in classify_batch
    json_with_trailer = (
        '{"primary_type": "tool", "tags": ["mcp"], "confidence": 0.88, '
        '"significance": "minor", "summary": "Test summary"}'
        "extra text here that breaks json.loads"
    )

    # Mock the Anthropic batch API: submit → retrieve → results
    mock_batch = MagicMock()
    mock_batch.id = "batch_test_123"
    mock_batch.processing_status = "ended"

    mock_msg = MagicMock()
    mock_msg.usage.input_tokens = 100
    mock_msg.usage.output_tokens = 50
    mock_msg.content = [MagicMock(text=json_with_trailer)]

    mock_result = MagicMock()
    mock_result.custom_id = "item-1"
    mock_result.result.type = "succeeded"
    mock_result.result.message = mock_msg

    async def _mock_results_iter(batch_id):
        async def _gen():
            yield mock_result

        return _gen()

    llm_client._mock_anthropic.messages.batches.create = AsyncMock(
        return_value=mock_batch
    )
    llm_client._mock_anthropic.messages.batches.retrieve = AsyncMock(
        return_value=mock_batch
    )
    llm_client._mock_anthropic.messages.batches.results = _mock_results_iter

    items = [{"custom_id": "item-1", "content": "Test content"}]
    results = await llm_client.classify_batch(items, "System prompt")

    assert "item-1" in results, f"Expected 'item-1' in results, got: {results}"
    llm_result = results["item-1"]
    assert llm_result.primary_type == "tool", (
        f"raw_decode fallback failed — primary_type is '{llm_result.primary_type}', "
        "expected 'tool'"
    )
    assert "mcp" in llm_result.tags
    assert llm_result.confidence == pytest.approx(0.88)
