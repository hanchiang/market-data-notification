import redis.asyncio as redis

from src.config import config

class Redis:
    redis: redis.Redis

    @staticmethod
    def get_client():
        return Redis.redis

    @staticmethod
    async def start_redis(script_mode=False):
        print('starting redis')
        host = config.get_redis_host()
        if script_mode:
            host = 'localhost'
        Redis.redis = await redis.Redis(host=host, port=config.get_redis_port(), db=config.get_redis_db())

    @staticmethod
    async def stop_redis():
        print('stopping redis')
        await Redis.redis.close()