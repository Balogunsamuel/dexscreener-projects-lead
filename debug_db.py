import asyncio
import aiosqlite

async def debug_db():
    async with aiosqlite.connect("data/leads.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT chain, token_address, token_symbol FROM tokens ORDER BY id DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
            print(f"Found {len(rows)} tokens in DB:")
            for row in rows:
                print(f" - {row['chain']}/{row['token_symbol']} ({row['token_address']})")

if __name__ == "__main__":
    asyncio.run(debug_db())
