import aiohttp

base_url = 'http://vixcentral.com'
headers = {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}


class HttpClient:
  instance = None

  def __init__(self):
    self.http_client = aiohttp.ClientSession(base_url=base_url,
                                             headers=headers)

  async def get(self, url: str, params={}):
    res = await self.http_client.get(url=url, params=params)
    return res

  @staticmethod
  def get_instance():
    if HttpClient.instance is None:
      HttpClient.instance = HttpClient()
    return HttpClient.instance


class HttpClientContextManager:

  def __enter__(self):
    self.session = HttpClient.get_instance()
    return self.session

  def __exit__(self, exc_type, exc_value, exc_tb):
    pass
