from src.http_client import HttpClient


class ThirdPartyVixCentralService:
  BASE_URL = 'http://vixcentral.com'
  HEADERS = {
    'Accept': 'application/json',
    'X-Requested-With': 'XMLHttpRequest'
  }

  def __init__(self):
    self.http_client = HttpClient(base_url=ThirdPartyVixCentralService.BASE_URL,
                                  headers=ThirdPartyVixCentralService.HEADERS)

  async def cleanup(self):
    await self.http_client.cleanup()

  # response: list of list
  # 0: list of 8 months. e.g. ["Jan", "Feb", ...]
  # 1: list of empty string
  # 2: list of last prices(we want to calculate the contango % for the first value). e.g. [23.6, 24.9, ...]
  # 8: list of VIX index(same values). e.g. [20.87, 20.87, ...]
  async def get_current(self):
    res = await self.http_client.get(url='/ajax_update')
    if res.status != 200:
      print(res.text)
      res.raise_for_status()
    res_json = await res.json()
    return res_json

  # date: yyyy-mm-dd
  # response: list of 10 numbers
  # first number is not used
  # we want to calculate the contango % for the second value
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
