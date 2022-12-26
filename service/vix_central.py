from http_client import HttpClient


class VixCentralService:
  BASE_URL = 'http://vixcentral.com'
  HEADERS = {
    'Accept': 'application/json',
    'X-Requested-With': 'XMLHttpRequest'
  }

  def __init__(self):
    self.http_client = HttpClient(base_url=VixCentralService.BASE_URL,
                                  headers=VixCentralService.HEADERS)

  async def get_current(self):
    res = await self.http_client.get(url='/ajax_update')
    if res.status != 200:
      print(res.text)
      res.raise_for_status()
    res_json = await res.json()
    print(res_json)
    return res_json

  # yyyy-mm-dd
  async def get_historical(self, date: str):
    res = await self.http_client.get(url='/ajax_historical',
                                     params={"n1": date})
    if res.status != 200:
      print(res.text)
      res.raise_for_status()

    res_json = await res.json()
    if res_json == "error":
      raise RuntimeError(f"no data found for {date}")
    return res_json
