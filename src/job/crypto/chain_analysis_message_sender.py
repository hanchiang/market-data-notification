from src.job.service.chainanalysis import format_chainanalysis
from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.market_data_type import MarketDataType

class ChainAnalysisMessageSender(MessageSenderWrapper):
    @property
    def data_source(self):
        return "ChainAnalysis"

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self):
        symbol = 'BTC'
        messages = []
        chainanalysis_service = Dependencies.get_chainanalysis_service()
        thirdparty_chainanalysis_service = Dependencies.get_thirdparty_chainanalysis_service()
        chainanalysis_trade_intensity = await thirdparty_chainanalysis_service.get_trade_intensity(symbol=symbol)
        chainanalysis_fees = await chainanalysis_service.get_fees(symbol=symbol)

        thirdparty_chainanalysis_message = format_chainanalysis(trade_intensity=chainanalysis_trade_intensity,
                                                                fees=chainanalysis_fees)
        if thirdparty_chainanalysis_message is not None:
            messages.append(thirdparty_chainanalysis_message)

        return messages



