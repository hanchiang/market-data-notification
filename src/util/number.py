def friendly_number(num: float) -> object:
    suffixes = ['', 'K', 'M', 'B', 'T']

    res = num
    count = 0
    while(abs(res / 1000) > 1):
        res = res / 1000
        count += 1
        if count == len(suffixes) - 1:
            break

    return f"{res} {suffixes[count]}"