import logging

from src.service.barchart import BarchartService
from src.service.crypto_sentiment import CryptoSentimentService
from src.service.stocks_sentiment import StocksSentimentService
from src.service.tradingview_service import TradingViewService
from src.http_client import HttpClient
from src.service.chainanalysis import ChainAnalysisService
from src.service.messari import MessariService
from src.third_party_service.barchart import ThirdPartyBarchartService
from src.third_party_service.chainanalysis import ThirdPartyChainAnalysisService
from src.third_party_service.messari import ThirdPartyMessariService
from src.third_party_service.vix_central import ThirdPartyVixCentralService
from src.service.vix_central import VixCentralService

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
  thirdparty_messari_service: ThirdPartyMessariService = None
  messari_service: MessariService = None
  thirdparty_chainanalysis_service: ThirdPartyChainAnalysisService = None
  chainanalysis_service: ChainAnalysisService = None
  crypto_sentiment_service: CryptoSentimentService = None

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
      messari_service_http_client = await HttpClient.create(base_url=ThirdPartyMessariService.BASE_URL)
      Dependencies.thirdparty_messari_service = ThirdPartyMessariService(http_client=messari_service_http_client)
      Dependencies.messari_service = MessariService(third_party_service=Dependencies.thirdparty_messari_service)

      chainanalysis_service_http_client = await HttpClient.create(base_url=ThirdPartyChainAnalysisService.BASE_URL)
      Dependencies.thirdparty_chainanalysis_service = ThirdPartyChainAnalysisService(http_client=chainanalysis_service_http_client)
      Dependencies.chainanalysis_service = ChainAnalysisService(third_party_service=Dependencies.thirdparty_chainanalysis_service)

      Dependencies.crypto_sentiment_service = CryptoSentimentService()

      Dependencies.is_initialised = True
      logger.info('Dependencies built')
    else:
      logger.info('Dependencies has already been initialised')

  @staticmethod
  async def cleanup():
    await Dependencies.vix_central_service.cleanup()
    await Dependencies.barchart_service.cleanup()
    await Dependencies.messari_service.cleanup()
    await Dependencies.chainanalysis_service.cleanup()

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
  def get_thirdparty_messari_service():
    return Dependencies.thirdparty_messari_service

  @staticmethod
  def get_messari_service():
    return Dependencies.messari_service

  @staticmethod
  def get_thirdparty_chainanalysis_service():
    return Dependencies.thirdparty_chainanalysis_service

  @staticmethod
  def get_chainanalysis_service():
    return Dependencies.chainanalysis_service

  @staticmethod
  def get_crypto_sentiment_service():
    return Dependencies.crypto_sentiment_service