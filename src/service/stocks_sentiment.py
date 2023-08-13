from statistics import mean
from typing import List

from market_data_library import CNNAPI
from market_data_library.stocks.cnn_fear_greed.type import CnnFearGreedIndex, FearAndGreedHistoricalData

import src.util.date_util as date_util
from src.config import config

from src.type.sentiment import FearGreedResult, FearGreedData, FearGreedAverage
from src.util.list_util import is_list_out_of_range
from src.util.logger import logger


class StocksSentimentService:
    def __init__(self):
        env = config.get_env()
        selenium_remote_mode = config.get_selenium_remote_mode()
        selenium_stealth = config.get_selenium_stealth()
        server_host = 'http://localhost:4444' if env == 'prod' else 'http://chrome:4444'
        cnn_api = CNNAPI(server_host=server_host, is_stealth=selenium_stealth, remote_mode=selenium_remote_mode)
        self.cnn_service = cnn_api.cnn_service
        self.cnc_type = cnn_api.cnn_type

    # TODO: cache
    async def get_stocks_fear_greed_index(self) -> FearGreedResult:
        fear_greed_res: CnnFearGreedIndex = await self.get_stocks_fear_greed_index_from_source()

        if fear_greed_res is None or fear_greed_res.fear_and_greed is None or fear_greed_res.fear_and_greed_historical is None:
            return None

        # data does not include weekends
        fear_and_greed_historical = fear_greed_res.fear_and_greed_historical
        fear_and_greed_historical_with_weekends = []


        # Fill in missing weekends with dummy data.
        # Note that calculating average historical results won't be accurate because weekends(i.e. dummy data) are included
        for i in range(0, len(fear_and_greed_historical.data)):
            historical_data = fear_and_greed_historical.data[i]
            dt = date_util.parse_timestamp(historical_data.x / 1000)
            fear_and_greed_historical_with_weekends.append(historical_data)
            # friday, add dummy data for saturday and sunday
            if dt.weekday() == 4:
                fear_and_greed_historical_with_weekends.append(historical_data)
                fear_and_greed_historical_with_weekends.append(historical_data)

        logger.info(
            f'Retrieved {len(fear_and_greed_historical.data)} historical data, adjusted with weekends to {len(fear_and_greed_historical_with_weekends)}')
        fear_and_greed_historical.data = fear_and_greed_historical_with_weekends


        data = self.transform_data(data=fear_greed_res)
        average = self.transform_average(data=fear_greed_res)
        res = FearGreedResult(data=data, average=average)

        return res


    def transform_data(self, data: CnnFearGreedIndex) -> List[FearGreedData]:
        parse_params = [
            {'text': 'Previous close', 'list_index': -1},
            {'text': 'Last week', 'list_index': -8},
            {'text': 'Last month', 'list_index': -31},
            {'text': 'Last 3 months', 'list_index': -91},
            {'text': 'Last year', 'list_index': self._get_last_year_list_index(data=data)},
        ]

        res: List[FearGreedData] = []

        for param in parse_params:
            if is_list_out_of_range(data=data.fear_and_greed_historical.data, index=param['list_index']):
                continue
            date = date_util.parse_timestamp(data.fear_and_greed_historical.data[param['list_index']].x / 1000)

            value = data.fear_and_greed_historical.data[param['list_index']].y
            sentiment = self.cnn_service.map_fear_greed_to_text(value=value)

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

    def transform_average(self, data: CnnFearGreedIndex) -> List[FearGreedAverage]:
        average_params = [
            {'timeframe': '1 week', 'list_end_index': -7},
            {'timeframe': '1 month', 'list_end_index': -30},
            {'timeframe': '3 months', 'list_end_index': -90},
            {'timeframe': '1 year', 'list_end_index': self._get_last_year_list_index(data=data)}
        ]

        res: List[FearGreedAverage] = []

        for param in average_params:
            if is_list_out_of_range(data=data.fear_and_greed_historical.data, index=param['list_end_index']):
                continue
            historical_range_data = data.fear_and_greed_historical.data[param['list_end_index']:-1]
            historical_score = list(map(lambda d: d.y, historical_range_data))
            average = mean(historical_score)
            sentiment = self.cnn_service.map_fear_greed_to_text(value=average)

            parsed = FearGreedAverage(
                timeframe=param['timeframe'],
                value=average
            )

            if sentiment is not None:
                parsed.sentiment_text = sentiment['text']
                parsed.emoji = sentiment['emoji']

            res.append(parsed)

        return res

    def _get_last_year_list_index(self, data: CnnFearGreedIndex):
        last_year_list_index = -365
        if len(data.fear_and_greed_historical.data) > 200 and len(data.fear_and_greed_historical.data) < abs(last_year_list_index):
            logger.info(
                f'Last year list index is {last_year_list_index} while length of fear greed historical data is {len(data.fear_and_greed_historical.data)}. Setting last year list index to 0')
            last_year_list_index = 0
        return last_year_list_index

    async def get_stocks_fear_greed_index_from_source(self) -> CnnFearGreedIndex:
        fear_greed_res: CnnFearGreedIndex = await self.cnn_service.get_fear_greed_index()
        return fear_greed_res

