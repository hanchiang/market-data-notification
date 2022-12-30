from src.http_client import HttpClient
from src.third_party_service.vix_central import ThirdPartyVixCentralService
from src.service.vix_central import VixCentralService

class Dependencies:
  is_initialised: bool = False
  thirdparty_vix_central_service: ThirdPartyVixCentralService = None
  vix_central_service: VixCentralService = None

  @staticmethod
  async def build():
    if not Dependencies.is_initialised:

      http_client = await HttpClient.create(base_url=ThirdPartyVixCentralService.BASE_URL, headers=ThirdPartyVixCentralService.HEADERS)
      Dependencies.thirdparty_vix_central_service = ThirdPartyVixCentralService(http_client=http_client)
      Dependencies.vix_central_service = VixCentralService(third_party_service=Dependencies.thirdparty_vix_central_service)

      Dependencies.is_initialised = True
      print('Dependencies built')
    else:
      print('Dependencies has already been initialised')

  @staticmethod
  async def cleanup():
    await Dependencies.thirdparty_vix_central_service.cleanup()
    await Dependencies.vix_central_service.cleanup()

  @staticmethod
  def get_thirdparty_vix_central_service():
    return Dependencies.thirdparty_vix_central_service

  @staticmethod
  def get_vix_central_service():
    return Dependencies.vix_central_service
