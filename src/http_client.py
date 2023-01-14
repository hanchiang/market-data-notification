import aiohttp
from aiohttp import ClientSession


class HttpClient:
  instance = None

  def __init__(self):
    self.client: ClientSession = None

  @staticmethod
  async def create(base_url: str, headers = {}):
    if not base_url:
      raise RuntimeError("base_url is required")

    instance = HttpClient()
    instance.client = aiohttp.ClientSession(base_url=base_url,
                                             headers=headers)
    return instance

  async def cleanup(self):
    await self.client.close()

  async def get(self, url: str, **kwargs):
    res = await self.client.get(url=url, **kwargs)
    return res


