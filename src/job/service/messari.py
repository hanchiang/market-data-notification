from src.service.messari import AssetMetrics
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown


def format_messari_metrics(res: AssetMetrics):
    curr = get_current_datetime()
    message = f"*Crypto market data at {escape_markdown(curr.strftime('%Y-%m-%d'))}:*\n"
    message = f"{message}*{res.symbol} price: {escape_markdown(str(res.price_usd))} USD*\n\n"

    if res.exchange_net_flows is not None:
        message = f"{message}*Exchange net flows:*\n"
        for exchange, usd_quantity in res.exchange_net_flows.items():
            message = f"{message}{exchange}: "
            for k, v in usd_quantity.items():
                message = f"{message}{k}: {escape_markdown(str(v))}, "
            message = message[:len(message)-3]
            message = f"{message}\n"
        message = f"{message}\n"

    if res.exchange_supply is not None:
        message = f"{message}*Exchange supply:*\n"
        for exchange, usd_quantity in res.exchange_supply.items():
            message = f"{message}{exchange}: "
            for k, v in usd_quantity.items():
                message = f"{message}{k}: {escape_markdown(str(v))}, "
            message = message[:len(message)-3]
            message = f"{message}\n"

    return message