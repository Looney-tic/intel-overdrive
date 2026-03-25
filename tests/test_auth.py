"""
FOUND-08: Auth service tests.

Proves:
- Key generation produces dti_v1_ format with 50+ chars
- hash_key is deterministic
- validate_key is timing-safe (uses secrets.compare_digest)
- validate_key returns True/False correctly
- validate_key_format accepts/rejects keys
- increment_usage and get_key_by_hash work with DB session
"""
import uuid
import pytest
from src.services.auth_service import AuthService
from src.models.models import User, APIKey


@pytest.fixture
def auth():
    """AuthService instance (stateless — no dependencies)."""
    return AuthService()


# ============================================================================
# Key generation
# ============================================================================


def test_generate_api_key_returns_raw_and_hash(auth):
    """FOUND-08: generate_api_key returns (raw, hash) tuple."""
    raw, hashed = auth.generate_api_key()
    assert isinstance(raw, str)
    assert isinstance(hashed, str)


def test_generate_api_key_raw_starts_with_dti_v1_(auth):
    """FOUND-08: Raw key starts with 'dti_v1_' prefix."""
    raw, _ = auth.generate_api_key()
    assert raw.startswith("dti_v1_"), f"Expected 'dti_v1_' prefix, got: {raw[:20]}"


def test_generate_api_key_anon_prefix(auth):
    """Anonymous keys use dti_v1_anon_ prefix."""
    raw, _ = auth.generate_api_key(prefix="dti_v1_anon_")
    assert raw.startswith(
        "dti_v1_anon_"
    ), f"Expected 'dti_v1_anon_' prefix, got: {raw[:20]}"
    # Anon keys should still pass format validation (dti_v1_ is a prefix of dti_v1_anon_)
    assert auth.validate_key_format(raw) is True


def test_generate_api_key_has_sufficient_entropy(auth):
    """FOUND-08: Raw key is at least 50 chars (32 bytes base64url + prefix = ~50+)."""
    raw, _ = auth.generate_api_key()
    assert len(raw) >= 50, f"Key too short: {len(raw)} chars"


def test_generate_api_key_produces_unique_keys(auth):
    """FOUND-08: Each call produces a different key."""
    keys = {auth.generate_api_key()[0] for _ in range(5)}
    assert len(keys) == 5, "Keys should be unique across calls"


# ============================================================================
# Hash key
# ============================================================================


def test_hash_key_is_deterministic(auth):
    """FOUND-08: Same input always produces same hash."""
    key = "dti_v1_test_key_for_determinism"
    hash1 = auth.hash_key(key)
    hash2 = auth.hash_key(key)
    assert hash1 == hash2


def test_hash_key_different_inputs_produce_different_hashes(auth):
    """FOUND-08: Different inputs produce different hashes."""
    h1 = auth.hash_key("dti_v1_key_one")
    h2 = auth.hash_key("dti_v1_key_two")
    assert h1 != h2


def test_hash_key_returns_hex_string(auth):
    """FOUND-08: hash_key returns 64-char hex string (SHA-256)."""
    hashed = auth.hash_key("dti_v1_any_key")
    assert len(hashed) == 64
    assert all(c in "0123456789abcdef" for c in hashed)


# ============================================================================
# Validate key
# ============================================================================


def test_validate_key_returns_true_for_correct_pair(auth):
    """FOUND-08: validate_key returns True for matching raw/hash pair."""
    raw, hashed = auth.generate_api_key()
    assert auth.validate_key(raw, hashed) is True


def test_validate_key_returns_false_for_wrong_key(auth):
    """FOUND-08: validate_key returns False when key doesn't match stored hash."""
    _, hashed = auth.generate_api_key()
    wrong_key = "dti_v1_this_is_not_the_right_key"
    assert auth.validate_key(wrong_key, hashed) is False


def test_validate_key_uses_timing_safe_comparison(auth):
    """
    FOUND-08: validate_key uses secrets.compare_digest (timing-safe).
    We test this by verifying the implementation uses compare_digest, not ==.
    """
    import inspect
    import secrets as secrets_module

    source = inspect.getsource(auth.validate_key)
    # Should contain compare_digest
    assert (
        "compare_digest" in source
    ), "validate_key must use secrets.compare_digest for timing safety"


# ============================================================================
# Validate key format
# ============================================================================


def test_validate_key_format_accepts_dti_v1_prefix(auth):
    """FOUND-08: validate_key_format accepts keys with dti_v1_ prefix."""
    assert auth.validate_key_format("dti_v1_sometoken123") is True


def test_validate_key_format_rejects_bad_prefix(auth):
    """FOUND-08: validate_key_format rejects keys without the correct prefix."""
    assert auth.validate_key_format("bad_key_format") is False
    assert auth.validate_key_format("sk-ant-something") is False
    assert auth.validate_key_format("") is False


# ============================================================================
# DB-dependent: increment_usage and get_key_by_hash
# ============================================================================


@pytest.fixture
async def user_with_api_key(session, auth):
    """Fixture: creates a User + APIKey in the test DB, returns (user, api_key, raw_key)."""
    user = User(
        id=uuid.uuid4(),
        email=f"test-{uuid.uuid4()}@example.com",
        is_active=True,
    )
    session.add(user)
    await session.flush()

    raw, hashed = auth.generate_api_key()
    api_key = APIKey(
        key_hash=hashed,
        key_prefix="dti_v1_",
        user_id=user.id,
        name="Test key",
        is_active=True,
        usage_count=0,
    )
    session.add(api_key)
    await session.commit()

    return user, api_key, raw, hashed


@pytest.mark.asyncio
async def test_get_key_by_hash_returns_api_key(session, auth, user_with_api_key):
    """FOUND-08: get_key_by_hash returns the APIKey for a known hash."""
    _, api_key, _, hashed = user_with_api_key
    result = await auth.get_key_by_hash(session, hashed)
    assert result is not None
    assert result.key_hash == hashed


@pytest.mark.asyncio
async def test_get_key_by_hash_returns_none_for_unknown_hash(
    session, auth, user_with_api_key
):
    """FOUND-08: get_key_by_hash returns None for unknown hash (with timing protection)."""
    result = await auth.get_key_by_hash(session, "a" * 64)
    assert result is None


@pytest.mark.asyncio
async def test_increment_usage_increments_counter(session, auth, user_with_api_key):
    """FOUND-08: increment_usage atomically increments usage_count."""
    _, api_key, _, hashed = user_with_api_key
    initial_count = api_key.usage_count

    await auth.increment_usage(session, hashed)

    # increment_usage commits internally; expire the session identity map cache
    # so the next query re-reads fresh data from the DB.
    session.expire_all()

    # Re-fetch to see updated value
    from sqlalchemy import select

    result = await session.execute(select(APIKey).where(APIKey.key_hash == hashed))
    updated_key = result.scalar_one()
    assert updated_key.usage_count == initial_count + 1
