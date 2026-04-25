import logging

from src.service.barchart import BarchartService
from src.service.crypto.cryptoquant import CryptoQuantService
from src.service.crypto.crypto_sentiment import CryptoSentimentService
from src.service.crypto.crypto_stats import CryptoStatsService
from src.service.stocks_sentiment import StocksSentimentService
from src.service.tradingview_service import TradingViewService
from src.http_client import HttpClient
from src.third_party_service.barchart import ThirdPartyBarchartService
from src.third_party_service.vix_central import ThirdPartyVixCentralService
from src.service.vix_central import VixCentralService
from src.data_source.market_data_library import cleanup_market_data_api

logger = logging.getLogger('Dependencies')
class Dependencies:
  is_initialised: bool = False

  # stocks
  tradingview_service: TradingViewService = None
  thirdparty_vix_central_service: ThirdPartyVixCentralService = None
  vix_central_service: VixCentralService = None
  thirdparty_barchart_service: ThirdPartyBarchartService = None
  barchart_service: BarchartService = None
  stocks_sentiment_service: StocksSentimentService = None

  # crypto
  cryptoquant_api_service: CryptoQuantService = None
  crypto_sentiment_service: CryptoSentimentService = None
  crypto_stats_service: CryptoStatsService = None

  @staticmethod
  async def build():
    if not Dependencies.is_initialised:
      # stocks
      Dependencies.tradingview_service = TradingViewService()

      vix_central_service_http_client = await HttpClient.create(base_url=ThirdPartyVixCentralService.BASE_URL, headers=ThirdPartyVixCentralService.HEADERS)
      Dependencies.thirdparty_vix_central_service = ThirdPartyVixCentralService(http_client=vix_central_service_http_client)
      Dependencies.vix_central_service = VixCentralService(third_party_service=Dependencies.thirdparty_vix_central_service)

      Dependencies.thirdparty_barchart_service = ThirdPartyBarchartService()
      Dependencies.barchart_service = BarchartService(third_party_service=Dependencies.thirdparty_barchart_service)

      Dependencies.stocks_sentiment_service = StocksSentimentService()

    # crypto
      Dependencies.cryptoquant_api_service = CryptoQuantService()
      Dependencies.crypto_sentiment_service = CryptoSentimentService()
      Dependencies.crypto_stats_service = CryptoStatsService()

      Dependencies.is_initialised = True
      logger.info('Dependencies built')
    else:
      logger.info('Dependencies has already been initialised')

  @staticmethod
  async def cleanup():
    if Dependencies.vix_central_service is not None:
      await Dependencies.vix_central_service.cleanup()
    await cleanup_market_data_api()

    Dependencies.is_initialised = False
    Dependencies.tradingview_service = None
    Dependencies.thirdparty_vix_central_service = None
    Dependencies.vix_central_service = None
    Dependencies.thirdparty_barchart_service = None
    Dependencies.barchart_service = None
    Dependencies.stocks_sentiment_service = None
    Dependencies.cryptoquant_api_service = None
    Dependencies.crypto_sentiment_service = None
    Dependencies.crypto_stats_service = None

  # stocks
  @staticmethod
  def get_tradingview_service():
    return Dependencies.tradingview_service

  @staticmethod
  def get_thirdparty_vix_central_service():
    return Dependencies.thirdparty_vix_central_service

  @staticmethod
  def get_vix_central_service():
    return Dependencies.vix_central_service

  @staticmethod
  def get_thirdparty_barchart_service():
    return Dependencies.thirdparty_barchart_service

  @staticmethod
  def get_barchart_service():
    return Dependencies.barchart_service

  @staticmethod
  def get_stocks_sentiment_service():
    return Dependencies.stocks_sentiment_service

  # crypto
  @staticmethod
  def get_cryptoquant_api_service():
    return Dependencies.cryptoquant_api_service

  @staticmethod
  def get_crypto_sentiment_service():
    return Dependencies.crypto_sentiment_service

  @staticmethod
  def get_crypto_stats_service():
    return Dependencies.crypto_stats_service
