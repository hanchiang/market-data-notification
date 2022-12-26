import aiohttp


class HttpClient:
  instance = None

  def __init__(self, base_url: str, headers={}):
    if not base_url:
      raise RuntimeError("base_url is required")
    self.client = aiohttp.ClientSession(base_url=base_url,
                                             headers=headers)

  async def cleanup(self):
    await self.client.close()

  async def get(self, url: str, params={}):
    res = await self.client.get(url=url, params=params)
    return res

  @staticmethod
  def get_instance():
    if HttpClient.instance is None:
      HttpClient.instance = HttpClient()
    return HttpClient.instance
