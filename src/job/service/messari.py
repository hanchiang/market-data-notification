from src.service.messari import AssetMetrics
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown
from src.util.number import friendly_number


def format_messari_metrics(res: AssetMetrics):
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
            message = message[:len(message)-2]
            message = f"{message}\n"
        message = f"{message}\n"

    if res.exchange_supply is not None:
        message = f"{message}*Exchange supply:*\n"
        for exchange, usd_quantity in res.exchange_supply.items():
            message = f"{message}{exchange}: "
            for k, v in usd_quantity.items():
                formatted_number = friendly_number(num=v, decimal_places=decimal_places)
                message = f"{message}{k}: {escape_markdown(formatted_number)}, "
            message = message[:len(message)-2]
            message = f"{message}\n"

    return message