import logging

from src.http_client import HttpClient


# Monday to friday, 24 hours
# See: https://www.cboe.com/tradable_products/vix/vix_futures/specifications/
logger = logging.getLogger('Third party vix central service')
class ThirdPartyVixCentralService:
  BASE_URL = 'http://vixcentral.com'
  HEADERS = {
    'Accept': 'application/json',
    'X-Requested-With': 'XMLHttpRequest'
  }

  def __init__(self, http_client: HttpClient):
    self.http_client = http_client

  async def cleanup(self):
    await self.http_client.cleanup()

  # `ajax_update` returns a nested list rather than a typed object.
  # 0: front-month labels from the provider, e.g. ["Apr", "May", ...]
  # 1: spacer strings
  # 2: live last-price strip; indices 0 and 1 are the current and next month
  #    prices used for contango
  # 8: repeated spot VIX index values
  async def get_current(self):
    res = await self.http_client.get(url='/ajax_update')
    if res.status != 200:
      logger.info(res.text)
      res.raise_for_status()
    res_json = await res.json()
    return res_json

  # date: yyyy-mm-dd
  # `ajax_historical` returns only numeric values, with no contract month labels.
  # Index 1 is the front month and index 2 is the next month for that historical
  # date, so downstream code has to infer contract identity separately.
  async def get_historical(self, date: str):
    res = await self.http_client.get(url='/ajax_historical',
                                     params={"n1": date})
    if res.status != 200:
      logger.info(res.text)
      res.raise_for_status()

    res_json = await res.json()
    if res_json == "error":
      raise RuntimeError(f"[third party vix central] no data found for {date}")
    return res_json
