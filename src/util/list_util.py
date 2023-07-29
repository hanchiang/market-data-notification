from typing import List, Any


def is_list_out_of_range(data: List[Any], index: int) -> bool:
    if data is None or len(data) == 0:
        return True
    if index == 0:
        return False
    if index > 0:
        return index >= len(data)
    return abs(index) > len(data)
