from statistics import mean
from typing import List, Any

from market_data_library import AlternativeMeAPI
from market_data_library.crypto.alternativeme.type import AlternativeMeFearGreedIndex

from src.util.date_util import parse
from src.util.list_util import is_list_out_of_range


class SentimentService:
    def __init__(self):
        self.alternativeme_service = AlternativeMeAPI().alternativeme_service

    # { average: [ {timeframe, value, sentiment_text, emoji } ], data: [{ relative_date_text, date, value, sentiment_text, emoji }] }
    async def get_crypto_fear_greed_index(self, from_source=False, days=365) -> AlternativeMeFearGreedIndex:
        if days is None or type(days) is not int:
            days = 365
        data: AlternativeMeFearGreedIndex = await self.alternativeme_service.get_fear_greed_index(days=days)

        if from_source:
            return data

        parse_params = [
            {'text': 'Now', 'list_index': -1},
            {'text': 'Yesterday', 'list_index': -2},
            {'text': 'Last week', 'list_index': -8},
            {'text': 'Last month', 'list_index': -31},
            {'text': 'Last 3 months', 'list_index': -91},
        ]

        res = { 'data': [], 'average': [] }
        if data.data.datasets is None or data.data.datasets[0].data is None:
            return None

        for parse_param in parse_params:
            if is_list_out_of_range(data=data.data.datasets[0].data, index=parse_param['list_index']):
                continue
            date = parse(dt=data.data.labels[parse_param['list_index']], format='%d %b, %Y')
            value = data.data.datasets[0].data[parse_param['list_index']]
            sentiment = self.alternativeme_service.map_fear_greed_to_text(value=value)

            parsed_res = {
                'relative_date_text': parse_param['text'],
                'date': date,
                'value': value
            }
            if sentiment is not None:
                parsed_res['sentiment_text'] = sentiment['text']
                parsed_res['emoji'] = sentiment['emoji']

            res['data'].append(parsed_res)

        summary_params = [
            {'timeframe': '7d', 'list_end_index': -7},
            {'timeframe': '30d', 'list_end_index': -30},
            {'timeframe': '90d', 'list_end_index': -90}
        ]

        for summary_param in summary_params:
            if is_list_out_of_range(data=data.data.datasets[0].data, index=summary_param['list_end_index']):
                continue
            average = mean(data.data.datasets[0].data[summary_param['list_end_index']:-1])
            sentiment = self.alternativeme_service.map_fear_greed_to_text(value=average)

            parsed_summary = {
                'timeframe': summary_param['timeframe'],
                'value': average
            }

            if sentiment is not None:
                parsed_summary['sentiment_text'] = sentiment['text']
                parsed_summary['emoji'] = sentiment['emoji']

            res['average'].append(parsed_summary)

        return res

