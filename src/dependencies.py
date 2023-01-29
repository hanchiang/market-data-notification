from src.http_client import HttpClient
from src.service.messari import MessariService
from src.third_party_service.chainanalysis import ThirdPartyChainanalysisService
from src.third_party_service.messari import ThirdPartyMessariService
from src.third_party_service.vix_central import ThirdPartyVixCentralService
from src.service.vix_central import VixCentralService

class Dependencies:
  is_initialised: bool = False
  thirdparty_vix_central_service: ThirdPartyVixCentralService = None
  vix_central_service: VixCentralService = None
  thirdparty_messari_service: ThirdPartyMessariService = None
  messari_service: MessariService = None
  thirdparty_chainanalysis_service: ThirdPartyChainanalysisService = None

  @staticmethod
  async def build():
    if not Dependencies.is_initialised:

      vix_central_service_http_client = await HttpClient.create(base_url=ThirdPartyVixCentralService.BASE_URL, headers=ThirdPartyVixCentralService.HEADERS)
      Dependencies.thirdparty_vix_central_service = ThirdPartyVixCentralService(http_client=vix_central_service_http_client)
      Dependencies.vix_central_service = VixCentralService(third_party_service=Dependencies.thirdparty_vix_central_service)

      messari_service_http_client = await HttpClient.create(base_url=ThirdPartyMessariService.BASE_URL)
      Dependencies.thirdparty_messari_service = ThirdPartyMessariService(http_client=messari_service_http_client)
      Dependencies.messari_service = MessariService(third_party_service=Dependencies.thirdparty_messari_service)

      chainanalysis_service_http_client = await HttpClient.create(base_url=ThirdPartyChainanalysisService.BASE_URL)
      Dependencies.thirdparty_chainanalysis_service = ThirdPartyChainanalysisService(http_client=chainanalysis_service_http_client)

      Dependencies.is_initialised = True
      print('Dependencies built')
    else:
      print('Dependencies has already been initialised')

  @staticmethod
  async def cleanup():
    await Dependencies.vix_central_service.cleanup()
    await Dependencies.messari_service.cleanup()

  @staticmethod
  def get_thirdparty_vix_central_service():
    return Dependencies.thirdparty_vix_central_service

  @staticmethod
  def get_vix_central_service():
    return Dependencies.vix_central_service

  @staticmethod
  def get_thirdparty_messari_service():
    return Dependencies.thirdparty_messari_service

  @staticmethod
  def get_messari_service():
    return Dependencies.messari_service

  @staticmethod
  def get_thirdparty_chainanalysis_service():
    return Dependencies.thirdparty_chainanalysis_service
