import asyncio
import logging
from dataclasses import dataclass
from typing import List

from src.third_party_service.chainanalysis import ThirdPartyChainAnalysisService

logger = logging.getLogger('Chain analysis servoce')

@dataclass
class UnitValue:
    value: float
    unit: str  # asset, usd


@dataclass
class FeesAverage:
    timeframe: str  # 7d, 30d, 90d, 180d
    values: List[UnitValue]


@dataclass
class FeesSummary:
    timeframe: str  # 1d
    current: float
    change: float
    percent_change: float
    unit: str  # asset

@dataclass
class RecentFees:
    ts: float
    values: List[UnitValue]


@dataclass
class ChainAnalysisFees:
    symbol: str
    highlight: str
    fees_summary: FeesSummary
    recent_fees: List[RecentFees]
    average_fees: List[FeesAverage]

class ChainAnalysisService:
    FEES_PAST_N_DAYS: float

    def __init__(self, third_party_service: ThirdPartyChainAnalysisService, fees_past_n_days=5):
        self.third_party_service = third_party_service
        ChainAnalysisService.FEES_PAST_N_DAYS = fees_past_n_days

    async def cleanup(self):
        await self.third_party_service.cleanup()

    async def get_fees(self, symbol: str) -> ChainAnalysisFees:
        symbol = symbol.upper()
        summary_coro = self.third_party_service.get_summary(symbol=symbol)
        fees_coro = self.third_party_service.get_fees(symbol=symbol)

        coros = [summary_coro, fees_coro]
        [summary, fees] = await asyncio.gather(*coros)

        generation_summary = summary.get('generation', [])
        fees_summary = next(filter(lambda x: x['name'].lower() == 'btc fees', generation_summary), None)
        if fees_summary is not None:
            logger.info(f'Fees summary for {symbol}: {fees_summary}')

        main_fees = fees.get('data', {}).get('main', {})
        unix_ts_list = list(main_fees.keys())
        unix_ts_list.reverse()
        unix_ts_list = unix_ts_list[:ChainAnalysisService.FEES_PAST_N_DAYS]

        secondary_fees = fees.get('data', {}).get('secondary', [])
        # 7d, 30d, 90d, 180d
        [fees_usd, fees_asset] = secondary_fees[0].get('values', [[], []])

        res = ChainAnalysisFees(
            symbol=symbol,
            highlight=fees.get('highlight'),
            fees_summary=FeesSummary(
                timeframe=fees_summary['time'], current=fees_summary['current'],
                change=fees_summary['change'], percent_change=fees_summary['percentChange'], unit=fees_summary['unit']
            ),
            recent_fees=list(map(lambda ts: RecentFees(ts=int(ts)//1000, values=[
                UnitValue(value=main_fees[ts][0]['values'][0], unit='usd'),
                UnitValue(value=main_fees[ts][0]['values'][1], unit='asset'),
            ]), unix_ts_list)),
            average_fees=[
                FeesAverage(timeframe='7d', values=[
                    UnitValue(value=fees_usd[0], unit='usd'),
                    UnitValue(value=fees_asset[0], unit='asset')
                ]),
                FeesAverage(timeframe='30d', values=[
                    UnitValue(value=fees_usd[1], unit='usd'),
                    UnitValue(value=fees_asset[1], unit='asset')
                ]),
                FeesAverage(timeframe='90d', values=[
                    UnitValue(value=fees_usd[2], unit='usd'),
                    UnitValue(value=fees_asset[2], unit='asset')
                ]),
                FeesAverage(timeframe='180d', values=[
                    UnitValue(value=fees_usd[3], unit='usd'),
                    UnitValue(value=fees_asset[3], unit='asset')
                ])
            ]
        )
        return res
