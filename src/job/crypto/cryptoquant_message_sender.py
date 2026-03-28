from market_data_library.types import cryptoquant_type

from src.config import config
from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown
from src.util.number import friendly_number


class CryptoQuantMessageSender(MessageSenderWrapper):
    @property
    def data_source(self):
        return "CryptoQuant"

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self):
        if not config.has_cryptoquant_api_token():
            return []

        service = Dependencies.get_cryptoquant_service()
        if service is None:
            return []

        messages = []
        curr = get_current_datetime()
        messages.append(f"*Crypto market data at {escape_markdown(curr.strftime('%Y-%m-%d'))}*")

        res = await service.get_asset_metrics()
        message = self._format_metrics(res)
        if message is not None:
            messages.append(message)

        return messages

    def _format_metrics(self, res: cryptoquant_type.AssetMetrics):
        res.sort_exchange_supply_and_net_flows_descending(absolute=True)

        message = f"*{res.symbol} price: {escape_markdown(friendly_number(num=res.price_usd, decimal_places=3))} USD*\n\n"
        decimal_places = 3

        if res.exchange_net_flows is not None:
            message = f"{message}*Exchange net flows:*\n"
            for exchange, usd_quantity in res.exchange_net_flows.items():
                message = f"{message}{exchange}: "
                for key, value in usd_quantity.items():
                    formatted_number = friendly_number(num=value, decimal_places=decimal_places)
                    message = f"{message}{key}: {escape_markdown(formatted_number)}, "
                message = message[:len(message) - 2]
                message = f"{message}\n"
            message = f"{message}\n"

        if res.exchange_supply is not None:
            message = f"{message}*Exchange supply:*\n"
            for exchange, usd_quantity in res.exchange_supply.items():
                message = f"{message}{exchange}: "
                for key, value in usd_quantity.items():
                    formatted_number = friendly_number(num=value, decimal_places=decimal_places)
                    message = f"{message}{key}: {escape_markdown(formatted_number)}, "
                message = message[:len(message) - 2]
                message = f"{message}\n"

        return message
