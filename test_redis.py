import redis.asyncio as redis
import asyncio

async def test():
    r = redis.from_url('redis://redis:6379/0')
    print(await r.ping())

asyncio.run(test())
