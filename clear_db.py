import asyncio
import argparse

import aiosqlite


async def clear_tokens(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("DELETE FROM admins")
        await db.execute("DELETE FROM wallets")
        await db.execute("DELETE FROM socials")
        await db.execute("DELETE FROM tokens")
        await db.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('admins', 'wallets', 'socials', 'tokens')"
        )
        await db.commit()
    print(f"Cleared all lead tables in {db_path}.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clear Dexscreener lead database tables")
    parser.add_argument(
        "--db-path",
        default="data/leads.db",
        help="Path to SQLite database (default: data/leads.db)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(clear_tokens(args.db_path))
