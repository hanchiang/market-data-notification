import enum


class CMCSectorSortBy(enum.Enum):
    AVG_PRICE_CHANGE = ('avg_price_change', 'average price change')
    MARKET_CAP_CHANGE = ('market_cap_change', 'market cap change')

class CMCSectorSortDirection(enum.Enum):
    DESCENDING = ('desc', 'descending')
    ASCENDING = ('asc', 'ascending')

class CMCSpotlightType(enum.Enum):
    TRENDING = 'trending'
    MOST_VISITED = 'most visited'
    RECENTLY_ADDED = 'recently added'
    GAINER_LIST = 'top gainer'
    LOSER_LIST = 'top loser'