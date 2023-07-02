import asyncio
import random

async def sleep(min_sec = 0.1, max_sec = 0.5):
    min_sec = min(min_sec, max_sec)
    max_sec = max(min_sec, max_sec)
    await asyncio.sleep(random.uniform(min_sec, max_sec))