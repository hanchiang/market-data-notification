from functools import reduce

from src.service.vix_central import RecentVixFuturesValues
from src.util.my_telegram import escape_markdown


# TODO: cleanup trading view webhook code
def format_vix_central_message(vix_central_value: RecentVixFuturesValues):
    if vix_central_value is None or len(vix_central_value.vix_futures_values) == 0:
        return None
    message = reduce(format_vix_futures_values, vix_central_value.vix_futures_values,
                     f"*VIX central data for {vix_central_value.vix_futures_values[0].futures_date} futures:*")
    if vix_central_value.is_contango_decrease_for_past_n_days:
        message = f"{message}\n*Contango has been decreasing for the past {vix_central_value.contango_decrease_past_n_days} days ‼️*"
    return message


def format_vix_futures_values(res, curr):
    message = f"{res}\ndate: {escape_markdown(curr.current_date)}, contango %: {escape_markdown(curr.formatted_contango)}"
    message = f"{message}" if curr.formatted_contango_change_prev_day is None else f"{message}, changed by {escape_markdown(curr.formatted_contango_change_prev_day)} from the previous day"
    if curr.is_contango_single_day_decrease_alert:
        threshold = f"{curr.contango_single_day_decrease_alert_ratio:.1%}"
        message = f"{message}, *which is greater than the threshold{escape_markdown(threshold)}* ‼️"
    return message

