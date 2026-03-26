"""
CLI script to create the first (or any) API key for Intel Overdrive.

Creates a User (if the email doesn't exist) and a new API key, then prints
the raw key to stdout. The raw key is NEVER logged — it is shown only once.

Usage:
    python scripts/create_api_key.py --email admin@example.com
    python scripts/create_api_key.py --email admin@example.com --name "my-agent-key"

The DATABASE_URL environment variable (or .env file) must be set.

After running, authenticate with:
    curl -H "X-API-Key: dti_v1_..." https://your-server/v1/feed
"""
import argparse
import asyncio
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.core.init_db as _db
from src.core.init_db import init_db, close_db
from src.services.auth_service import AuthService
from src.models.models import APIKey, User
from sqlalchemy import select


async def create_api_key(email: str, name: str | None = None) -> None:
    """Create a user (if needed) and a new API key, print the raw key."""
    await init_db()

    auth = AuthService()
    raw_key, key_hash = auth.generate_api_key()
    key_prefix = raw_key[:7]  # "dti_v1_"

    async with _db.async_session_factory() as session:
        # Find or create user
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user is None:
            user = User(email=email, is_active=True, profile={})
            session.add(user)
            await session.flush()
            print(f"Created new user: {email}", file=sys.stderr)
        else:
            print(f"Found existing user: {email} (id={user.id})", file=sys.stderr)

        # Create API key
        api_key = APIKey(
            key_hash=key_hash,
            key_prefix=key_prefix,
            user_id=user.id,
            name=name,
            is_active=True,
        )
        session.add(api_key)
        await session.commit()

        print(f"Created API key (id={api_key.id})", file=sys.stderr)

    await close_db()

    # Print the raw key to stdout — clearly labeled
    print()
    print("=" * 60)
    print("API KEY CREATED SUCCESSFULLY")
    print("=" * 60)
    print(f"Key:  {raw_key}")
    print(f"Name: {name or '(unnamed)'}")
    print()
    print("IMPORTANT: Store this key securely — it cannot be retrieved again.")
    print("Authenticate with:  X-API-Key: " + raw_key)
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create an Intel Overdrive API key.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Email address for the user account (created if it does not exist).",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Optional label for the API key (e.g. 'my-agent', 'cursor').",
    )
    args = parser.parse_args()

    asyncio.run(create_api_key(email=args.email, name=args.name))


if __name__ == "__main__":
    main()
