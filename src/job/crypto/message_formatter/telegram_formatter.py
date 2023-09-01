from src.type.sentiment import FearGreedResult
from src.util.my_telegram import escape_markdown


def crypto_sentiment_message_formatter(res: FearGreedResult, should_escape=True):
    message = 'Sentiment:\n'
    for data in res.data:
        message = f'{message}{data.relative_date_text}: {data.sentiment_text}{escape_markdown("(", should_escape=should_escape)}{data.value} {data.emoji}{escape_markdown(")", should_escape=should_escape)}\n'

    message = f'{message}\n'
    message = f'{message}Average:\n'
    for average in res.average:
        rounded_value = int(round(average.value, 0))
        message = f'{message}Timeframe: {average.timeframe}: {average.sentiment_text}{escape_markdown("(", should_escape=should_escape)}{rounded_value} {average.emoji}{escape_markdown(")", should_escape=should_escape)}\n'
    return message