import asyncio
import aiosqlite

async def clear_tokens():
    async with aiosqlite.connect("data/leads.db") as db:
        await db.execute("DELETE FROM tokens")
        await db.commit()
    print("✅ Cleared all tokens from database. The bot will re-discover everything now.")

if __name__ == "__main__":
    asyncio.run(clear_tokens())
