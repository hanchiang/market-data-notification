from service.vix_central import VixCentralService


class Dependencies:
  vix_central_service = None
  is_initialised = False

  @staticmethod
  def build():
    if not Dependencies.is_initialised:
      Dependencies.vix_central_service = VixCentralService()
      Dependencies.is_initialised = True
      print('Dependencies built')
    else:
      print('Dependencies has already been initialised')

  @staticmethod
  def get_vix_central_service():
    return Dependencies.vix_central_service
