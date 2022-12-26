from src.service.vix_central import VixCentralService


class Dependencies:
  vix_central_service: VixCentralService = None
  is_initialised: bool = False

  @staticmethod
  def build():
    if not Dependencies.is_initialised:
      Dependencies.vix_central_service = VixCentralService()
      Dependencies.is_initialised = True
      print('Dependencies built')
    else:
      print('Dependencies has already been initialised')

  @staticmethod
  async def cleanup():
    await Dependencies.vix_central_service.cleanup()

  @staticmethod
  def get_vix_central_service():
    return Dependencies.vix_central_service
