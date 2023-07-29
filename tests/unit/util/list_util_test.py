import datetime

import pytest

from src.util import list_util


class TestDateUtil:
    @pytest.mark.parametrize('data, index, expected', [
        ([], 0, True),
        (None, 0, True),
        ([1], 0, False),
        ([1], 1, True),
        ([1], -1, False),
        ([1], -2, True)
    ])
    def test_is_list_out_of_range(self, data, index, expected):
        res = list_util.is_list_out_of_range(data=data, index=index)
        assert res == expected