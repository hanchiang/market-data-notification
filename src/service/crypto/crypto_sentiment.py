import logging
from statistics import mean
from typing import List

from market_data_library import AlternativeMeAPI
from market_data_library.crypto.alternativeme.type import AlternativeMeFearGreedIndex

from src.type.sentiment import FearGreedResult, FearGreedData, FearGreedAverage
from src.util.date_util import parse
from src.util.list_util import is_list_out_of_range

logger = logging.getLogger('Crypto sentiment service')
class CryptoSentimentService:
    def __init__(self):
        self.alternativeme_service = AlternativeMeAPI().alternativeme_service

    # { average: [ {timeframe, value, sentiment_text, emoji } ], data: [{ relative_date_text, date, value, sentiment_text, emoji }] }
    async def get_crypto_fear_greed_index(self, days=365) -> FearGreedResult:
        fear_greed_res: AlternativeMeFearGreedIndex = await self.get_crypto_fear_greed_index_from_source(days=days)

        if fear_greed_res.data.datasets is None or fear_greed_res.data.datasets[0].data is None:
            return None

        data = self.transform_data(data=fear_greed_res)
        average = self.transform_average(data=fear_greed_res)
        res = FearGreedResult(data=data, average=average)

        return res

    def transform_data(self, data: AlternativeMeFearGreedIndex) -> List[FearGreedData]:
        parse_params = [
            {'text': 'Now', 'list_index': -1},
            {'text': 'Yesterday', 'list_index': -2},
            {'text': 'Last week', 'list_index': -8},
            {'text': 'Last month', 'list_index': -31},
            {'text': 'Last 3 months', 'list_index': -91},
            {'text': 'Last year', 'list_index': self._get_last_year_list_index(data=data)}
        ]

        res: List[FearGreedData] = []

        for param in parse_params:
            if is_list_out_of_range(data=data.data.datasets[0].data, index=param['list_index']):
                continue
            date = parse(dt=data.data.labels[param['list_index']], format='%d %b, %Y')
            value = data.data.datasets[0].data[param['list_index']]
            sentiment = self.alternativeme_service.map_fear_greed_to_text(value=value)

            parsed = FearGreedData(
                relative_date_text=param['text'],
                date=date,
                value=value
            )
            if sentiment is not None:
                parsed.sentiment_text = sentiment['text']
                parsed.emoji = sentiment['emoji']

            res.append(parsed)
        return res

    def transform_average(self, data: AlternativeMeFearGreedIndex) -> List[FearGreedAverage]:
        average_params = [
            {'timeframe': '7d', 'list_end_index': -7},
            {'timeframe': '30d', 'list_end_index': -30},
            {'timeframe': '90d', 'list_end_index': -90},
            {'timeframe': '1 year', 'list_end_index': self._get_last_year_list_index(data=data)}
        ]

        res: List[FearGreedAverage] = []

        for param in average_params:
            if is_list_out_of_range(data=data.data.datasets[0].data, index=param['list_end_index']):
                continue
            average = mean(data.data.datasets[0].data[param['list_end_index']:-1])
            sentiment = self.alternativeme_service.map_fear_greed_to_text(value=average)

            parsed = FearGreedAverage(
                timeframe=param['timeframe'],
                value=average
            )

            if sentiment is not None:
                parsed.sentiment_text = sentiment['text']
                parsed.emoji = sentiment['emoji']

            res.append(parsed)

        return res

    def _get_last_year_list_index(self, data: AlternativeMeFearGreedIndex):
        last_year_list_index = -365
        if len(data.data.datasets) > 200 and len(data.fear_and_greed_historical.data) < abs(last_year_list_index):
            logger.info(
                f'Last year list index is {last_year_list_index} while length of fear greed index is {len(data.fear_and_greed_historical.data)}. Setting last year list index to 0')
            last_year_list_index = 0
        return last_year_list_index


    # returns data in chronological order
    async def get_crypto_fear_greed_index_from_source(self, days=365) -> AlternativeMeFearGreedIndex:
        if days is None or type(days) is not int:
            days = 365
        data = await self.alternativeme_service.get_fear_greed_index(days=days)
        return data

