import enum

# Indicator the threshold level of each metric type, in ratio. 0.5 = 50%
class MetricTypeIndicator(enum.Enum):
    COIN_PRICE_CHANGE_24H = ('price_change_24h', 0.5, 1, 2)
    COIN_VOLUME_CHANGE_24H = ('volume_change_24h', 1, 2, 4)
    COIN_MARKET_CAP_CHANGE_24H = ('volume_change_24h', 0.15, 0.3, 0.5)
    SECTOR_PRICE_CHANGE_24H = ('price_change_24h', 0.3, 0.5, 1)
    SECTOR_VOLUME_CHANGE_24H = ('volume_change_24h', 0.5, 1, 2)
    SECTOR_MARKET_CAP_CHANGE_24H = ('volume_change_24h', 0.15, 0.3, 0.5)
