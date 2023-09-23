import pytest
from src.job.crypto.util import bold_text_for_metric_type
from src.type.metric_type import MetricTypeIndicator

class TestCryptoJobUtil:
    @pytest.mark.parametrize('metric_type, value, expected', [
        (MetricTypeIndicator.COIN_PRICE_CHANGE_24H, 0.01, ''),
        (MetricTypeIndicator.COIN_PRICE_CHANGE_24H, -0.01, ''),
        (MetricTypeIndicator.COIN_PRICE_CHANGE_24H, 0.5, '❗'),
        (MetricTypeIndicator.COIN_PRICE_CHANGE_24H, -0.5, '❗'),
        (MetricTypeIndicator.COIN_PRICE_CHANGE_24H, 1, '❗❗'),
        (MetricTypeIndicator.COIN_PRICE_CHANGE_24H, -1, '❗❗'),
        (MetricTypeIndicator.COIN_PRICE_CHANGE_24H, 2, '❗❗❗'),
        (MetricTypeIndicator.COIN_PRICE_CHANGE_24H, -2, '❗❗❗')
    ])
    def test_bold_text_for_metric_type(self, metric_type: MetricTypeIndicator, value: float, expected: str):
        res = bold_text_for_metric_type(metric_type=metric_type, value=value)

        assert res == expected