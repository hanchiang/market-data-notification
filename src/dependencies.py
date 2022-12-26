from src.third_party_service.vix_central import ThirdPartyVixCentralService
from src.service.vix_central import VixCentralService

class Dependencies:
  is_initialised: bool = False
  thirdparty_vix_central_service: ThirdPartyVixCentralService = None
  vix_central_service: VixCentralService = None

  @staticmethod
  def build():
    if not Dependencies.is_initialised:
      Dependencies.thirdparty_vix_central_service = ThirdPartyVixCentralService()
      Dependencies.vix_central_service = VixCentralService()

      Dependencies.is_initialised = True
      print('Dependencies built')
    else:
      print('Dependencies has already been initialised')

  @staticmethod
  async def cleanup():
    await Dependencies.thirdparty_vix_central_service.cleanup()

  @staticmethod
  def get_thirdparty_vix_central_service():
    return Dependencies.thirdparty_vix_central_service

  @staticmethod
  def get_vix_central_service():
    return Dependencies.vix_central_service
