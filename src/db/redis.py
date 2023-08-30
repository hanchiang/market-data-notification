import logging

import redis.asyncio as redis

from src.config import config

logger = logging.getLogger('Redis')
class Redis:
    redis: redis.Redis

    @staticmethod
    def get_client():
        return Redis.redis

    @staticmethod
    async def start_redis():
        logger.info('starting redis')
        host = config.get_redis_host()
        Redis.redis = await redis.Redis(host=host, port=config.get_redis_port(), db=config.get_redis_db())

    @staticmethod
    async def stop_redis():
        logger.info('stopping redis')
        await Redis.redis.close()