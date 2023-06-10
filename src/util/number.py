def friendly_number(num: float, decimal_places = 2) -> str:
    suffixes = ['', 'K', 'M', 'B', 'T']

    res = num
    count = 0
    while(abs(res / 1000) > 1):
        res = res / 1000
        count += 1
        if count == len(suffixes) - 1:
            break

    if decimal_places is not None and decimal_places > 0:
        res = round(res, decimal_places)
    return f"{res} {suffixes[count]}"