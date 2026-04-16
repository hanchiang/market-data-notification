from unittest.mock import AsyncMock, Mock

import pytest

from src.dependencies import Dependencies


@pytest.fixture(autouse=True)
def reset_dependencies_state():
    original_values = {
        "is_initialised": Dependencies.is_initialised,
        "tradingview_service": Dependencies.tradingview_service,
        "thirdparty_vix_central_service": Dependencies.thirdparty_vix_central_service,
        "vix_central_service": Dependencies.vix_central_service,
        "thirdparty_barchart_service": Dependencies.thirdparty_barchart_service,
        "barchart_service": Dependencies.barchart_service,
        "stocks_sentiment_service": Dependencies.stocks_sentiment_service,
        "cryptoquant_api_service": Dependencies.cryptoquant_api_service,
        "crypto_sentiment_service": Dependencies.crypto_sentiment_service,
        "crypto_stats_service": Dependencies.crypto_stats_service,
    }

    Dependencies.is_initialised = False
    Dependencies.tradingview_service = None
    Dependencies.thirdparty_vix_central_service = None
    Dependencies.vix_central_service = None
    Dependencies.thirdparty_barchart_service = None
    Dependencies.barchart_service = None
    Dependencies.stocks_sentiment_service = None
    Dependencies.cryptoquant_api_service = None
    Dependencies.crypto_sentiment_service = None
    Dependencies.crypto_stats_service = None

    try:
        yield
    finally:
        for name, value in original_values.items():
            setattr(Dependencies, name, value)


@pytest.mark.asyncio
async def test_build_and_cleanup_wire_dependencies_without_live_clients(monkeypatch):
    tradingview_service = Mock()
    vix_http_client = Mock()
    thirdparty_vix_central_service = Mock()
    thirdparty_barchart_service = Mock()
    vix_central_service = Mock()
    vix_central_service.cleanup = AsyncMock()
    barchart_service = Mock()
    barchart_service.cleanup = AsyncMock()
    stocks_sentiment_service = Mock()
    cryptoquant_underlying_service = Mock()
    cryptoquant_underlying_service.cleanup = AsyncMock()
    cryptoquant_service = Mock(cryptoquant_service=cryptoquant_underlying_service)
    crypto_sentiment_service = Mock()
    crypto_stats_service = Mock()

    build_http_client = AsyncMock(return_value=vix_http_client)
    tradingview_cls = Mock(return_value=tradingview_service)
    thirdparty_vix_cls = Mock(return_value=thirdparty_vix_central_service)
    vix_central_cls = Mock(return_value=vix_central_service)
    thirdparty_barchart_cls = Mock(return_value=thirdparty_barchart_service)
    barchart_cls = Mock(return_value=barchart_service)
    stocks_sentiment_cls = Mock(return_value=stocks_sentiment_service)
    cryptoquant_cls = Mock(return_value=cryptoquant_service)
    crypto_sentiment_cls = Mock(return_value=crypto_sentiment_service)
    crypto_stats_cls = Mock(return_value=crypto_stats_service)

    monkeypatch.setattr("src.dependencies.HttpClient.create", build_http_client)
    monkeypatch.setattr("src.dependencies.TradingViewService", tradingview_cls)
    monkeypatch.setattr(
        "src.dependencies.ThirdPartyVixCentralService",
        thirdparty_vix_cls,
    )
    monkeypatch.setattr("src.dependencies.VixCentralService", vix_central_cls)
    monkeypatch.setattr(
        "src.dependencies.ThirdPartyBarchartService",
        thirdparty_barchart_cls,
    )
    monkeypatch.setattr("src.dependencies.BarchartService", barchart_cls)
    monkeypatch.setattr(
        "src.dependencies.StocksSentimentService",
        stocks_sentiment_cls,
    )
    monkeypatch.setattr("src.dependencies.CryptoQuantService", cryptoquant_cls)
    monkeypatch.setattr(
        "src.dependencies.CryptoSentimentService",
        crypto_sentiment_cls,
    )
    monkeypatch.setattr("src.dependencies.CryptoStatsService", crypto_stats_cls)

    await Dependencies.build()
    await Dependencies.build()

    assert Dependencies.is_initialised is True
    build_http_client.assert_awaited_once()
    tradingview_cls.assert_called_once_with()
    thirdparty_vix_cls.assert_called_once_with(http_client=vix_http_client)
    vix_central_cls.assert_called_once_with(
        third_party_service=thirdparty_vix_central_service
    )
    thirdparty_barchart_cls.assert_called_once_with()
    barchart_cls.assert_called_once_with(
        third_party_service=thirdparty_barchart_service
    )
    stocks_sentiment_cls.assert_called_once_with()
    cryptoquant_cls.assert_called_once_with()
    crypto_sentiment_cls.assert_called_once_with()
    crypto_stats_cls.assert_called_once_with()

    assert Dependencies.get_tradingview_service() is tradingview_service
    assert Dependencies.get_vix_central_service() is vix_central_service
    assert Dependencies.get_barchart_service() is barchart_service
    assert Dependencies.get_stocks_sentiment_service() is stocks_sentiment_service
    assert Dependencies.get_cryptoquant_api_service() is cryptoquant_service
    assert Dependencies.get_crypto_sentiment_service() is crypto_sentiment_service
    assert Dependencies.get_crypto_stats_service() is crypto_stats_service

    await Dependencies.cleanup()

    vix_central_service.cleanup.assert_awaited_once()
    barchart_service.cleanup.assert_awaited_once()
    cryptoquant_underlying_service.cleanup.assert_awaited_once()
