import asyncio


async def test():
    return 123


print(asyncio.run(test()))
