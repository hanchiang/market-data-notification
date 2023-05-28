from src.service.messari import AssetMetrics
from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown
from src.type.market_data_type import MarketDataType
from src.util.number import friendly_number

class MessariMessageSender(MessageSenderWrapper):
    @property
    def data_source(self):
        return "Messari"

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self):
        messages = []
        curr = get_current_datetime()
        messages.append(f"*Crypto market data at {escape_markdown(curr.strftime('%Y-%m-%d'))}*")

        messari_service = Dependencies.get_messari_service()
        messari_res = await messari_service.get_asset_metrics()
        messari_message = self._format_messari_metrics(messari_res)
        if messari_message is not None:
            messages.append(messari_message)

        return messages

    def _format_messari_metrics(self, res: AssetMetrics):
        res.sort_exchange_supply_and_net_flows_descending(absolute=True)

        message = f"*{res.symbol} price: {escape_markdown(friendly_number(num=res.price_usd, decimal_places=3))} USD*\n\n"
        decimal_places = 3

        if res.exchange_net_flows is not None:
            message = f"{message}*Exchange net flows:*\n"
            for exchange, usd_quantity in res.exchange_net_flows.items():
                message = f"{message}{exchange}: "
                for k, v in usd_quantity.items():
                    formatted_number = friendly_number(num=v, decimal_places=decimal_places)
                    message = f"{message}{k}: {escape_markdown(formatted_number)}, "
                message = message[:len(message) - 2]
                message = f"{message}\n"
            message = f"{message}\n"

        if res.exchange_supply is not None:
            message = f"{message}*Exchange supply:*\n"
            for exchange, usd_quantity in res.exchange_supply.items():
                message = f"{message}{exchange}: "
                for k, v in usd_quantity.items():
                    formatted_number = friendly_number(num=v, decimal_places=decimal_places)
                    message = f"{message}{k}: {escape_markdown(formatted_number)}, "
                message = message[:len(message) - 2]
                message = f"{message}\n"

        return message

