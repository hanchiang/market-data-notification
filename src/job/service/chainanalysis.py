from functools import reduce

from src.service.chainanalysis import ChainAnalysisFees, RecentFees, FeesAverage
from src.util import date_util
from src.util.my_telegram import escape_markdown
from src.util.number import friendly_number

def format_chainanalysis(trade_intensity: dict, fees: ChainAnalysisFees):
    message = f"*{escape_markdown(trade_intensity.get('highlight', ''))}*\n"
    message = f"{message}\n{format_fees_summary(fees)}\n"
    message = f"{message}\n{format_recent_fees(fees)}\n"
    message = f"{message}\n{format_average_fees(fees)}"
    return message

def format_fees_summary(fees: ChainAnalysisFees) -> str:
    percent_change = f'{escape_markdown(f"({fees.fees_summary.percent_change/100:.2%})")}'
    return f"*Fees summary*:\n{escape_markdown(fees.highlight)}, change of {escape_markdown(str(fees.fees_summary.change))}{percent_change} in {fees.fees_summary.timeframe}"

def format_recent_fees(fees: ChainAnalysisFees) -> str:
    return reduce(format_recent_fee, fees.recent_fees, '*Recent fees*:')

def format_recent_fee(res: str, fee: RecentFees) -> str:
    dt = date_util.get_datetime_from_timestamp(fee.ts, use_ny_tz=False)
    formatted_date = escape_markdown(date_util.format(dt))
    asset_amount = escape_markdown(f"{friendly_number(num=fee.values[1].value, decimal_places=2)}")
    usd_amount = escape_markdown(f"({friendly_number(num=fee.values[0].value, decimal_places=3)} USD)")
    message = f"date: {formatted_date}, amount: {asset_amount}{usd_amount}"

    return f"{res}\n{message}"

def format_average_fees(fees: ChainAnalysisFees) -> str:
    return reduce(format_average_fee, fees.average_fees, '*Average fees*:')

def format_average_fee(res: str, fee: FeesAverage) -> str:
    asset_amount = escape_markdown(f"{friendly_number(num=fee.values[1].value, decimal_places=2)}")
    usd_amount = escape_markdown(f"({friendly_number(num=fee.values[0].value, decimal_places=3)} USD)")
    message = f"timeframe: {fee.timeframe}, amount: {asset_amount}{usd_amount}"

    return f"{res}\n{message}"