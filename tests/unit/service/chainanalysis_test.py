import asyncio
from unittest.mock import patch, AsyncMock, Mock

import pytest

from src.dependencies import Dependencies
from src.service.crypto.chainanalysis import ChainAnalysisService, FeesSummary, RecentFees, UnitValue, FeesAverage


class TestChainAnalysisService:
    summary = {
        "demand": [
            {
                "name": "Total BTC flows",
                "time": "1d",
                "current": 79211.8080094017,
                "change": 10814.99815416,
                "percentChange": 15.8121382811,
                "unit": "asset"
            },
        ],
        "risk": [
            {
                "name": "Illicit BTC flows as % of total flows",
                "time": "1d",
                "current": 0.1768656106,
                "change": 0.0665728086,
                "percentChange": 60.3600664699,
                "unit": "percent"
            },
        ],
        "supply": [
            {
                "name": "BTC held for less than 1 year",
                "time": "1wk",
                "current": 6855171.30820795,
                "change": -15478.062681281,
                "percentChange": -0.2252780173,
                "unit": "asset"
            },
        ],
        "generation": [
            {
                "name": "BTC fees",
                "time": "1d",
                "current": 134.96713092,
                "change": 21.49068075,
                "percentChange": 18.938449976,
                "unit": "asset"
            }
        ],
        "trading": [
            {
                "name": "BTC close price",
                "time": "1d",
                "current": 29550.84,
                "change": 684.3,
                "percentChange": 2.3705646745,
                "unit": "usd"
            },
        ]
    }
    fees = {
        "highlight": "BTC fees in the last day are 134.97 BTC, the highest level in over 365 days",
        "data": {
            "main": {
                "1683158400000": [
                    {
                        "name": "fees",
                        "values": [
                            3289652.82692,
                            113.47645017
                        ]
                    }
                ],
                "1683244800000": [
                    {
                        "name": "fees",
                        "values": [
                            3945402.86847,
                            134.96713092
                        ]
                    }
                ]
            },
            "secondary": [
                {
                    "name": "fees",
                    "values": [
                        [
                            2664439.374441429,
                            1174680.1891596667,
                            831486.2028334443,
                            581797.041176
                        ],
                        [
                            92.36133746428573,
                            40.63418887833333,
                            31.271207405555554,
                            24.823413400055557
                        ]
                    ]
                }
            ]
        }
    }

    @classmethod
    def setup_class(cls):
        asyncio.run(Dependencies.build())

    @classmethod
    def teardown_class(cls):
        asyncio.run(Dependencies.cleanup())

    @pytest.mark.asyncio
    @patch("asyncio.gather", new_callable=AsyncMock)
    async def test_get_fees(self, asyncio_gather_mock: AsyncMock):
        thirdparty_chainanalysis_service = Dependencies.get_thirdparty_chainanalysis_service()
        thirdparty_chainanalysis_service.get_fees = Mock()
        thirdparty_chainanalysis_service.get_summary = Mock()

        service = ChainAnalysisService(third_party_service=thirdparty_chainanalysis_service, fees_past_n_days=2)
        asyncio_gather_mock.return_value = [self.summary, self.fees]
        res = await service.get_fees(symbol='BTC')

        expected_fees_summary = FeesSummary(
            timeframe='1d', current=134.96713092,
            change=21.49068075, percent_change=18.938449976, unit='asset'
        )
        expected_recent_fees = [
            RecentFees(
                ts=1683244800,
                values=[
                    UnitValue(value=3945402.86847, unit='usd'),
                    UnitValue(value=134.96713092, unit='asset')
                ]
            ),
            RecentFees(
                ts=1683158400,
                values=[
                    UnitValue(value=3289652.82692, unit='usd'),
                    UnitValue(value=113.47645017, unit='asset')
                ]
            )
        ]
        expected_average_fees = [
            FeesAverage(timeframe='7d', values=[
                UnitValue(value=2664439.374441429, unit='usd'),
                UnitValue(value=92.36133746428573, unit='asset'),
            ]),
            FeesAverage(timeframe='30d', values=[
                UnitValue(value=1174680.1891596667, unit='usd'),
                UnitValue(value=40.63418887833333, unit='asset'),
            ]),
            FeesAverage(timeframe='90d', values=[
                UnitValue(value=831486.2028334443, unit='usd'),
                UnitValue(value=31.271207405555554, unit='asset'),
            ]),
            FeesAverage(timeframe='180d', values=[
                UnitValue(value=581797.041176, unit='usd'),
                UnitValue(value=24.823413400055557, unit='asset'),
            ])
        ]

        thirdparty_chainanalysis_service.get_fees.assert_called_once()
        thirdparty_chainanalysis_service.get_summary.assert_called_once()
        asyncio_gather_mock.assert_awaited()
        assert res.symbol == 'BTC'
        assert res.highlight == 'BTC fees in the last day are 134.97 BTC, the highest level in over 365 days'
        assert res.fees_summary == expected_fees_summary
        assert res.recent_fees == expected_recent_fees
        assert res.average_fees == expected_average_fees