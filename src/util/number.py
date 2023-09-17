import decimal


def friendly_number(num: float, decimal_places = 2) -> str:
    suffixes = ['', 'K', 'M', 'B', 'T']
    num_non_zero_digits = 4

    dec = decimal.Decimal(str(num))
    (sign, digits, exponent) = dec.as_tuple()

    if abs(exponent) > 0 and dec.compare(decimal.Decimal('1000')) < 0:
        num_leading_decimal_zeros = count_leading_decimal_zeros(dec)
        decimal_places = min(abs(exponent), num_leading_decimal_zeros + num_non_zero_digits)
        res = round(num, decimal_places)
        return format_precision_without_trailing_zero(res, places=abs(exponent))

    # For values >= 1000
    res = num
    count = 0
    while(abs(res / 1000) >= 1):
        res = res / 1000
        count += 1
        if count == len(suffixes) - 1:
            break

    if decimal_places is not None and decimal_places >= 0:
        res = round(res, decimal_places)

    res = format_precision_without_trailing_zero(res, places=abs(decimal_places))
    if count > 0:
        return f"{res} {suffixes[count]}"
    else:
        return res

def count_leading_decimal_zeros(num: decimal.Decimal):
    if num.compare(decimal.Decimal('0')) == 0:
        return 0
    # format according to precision instead of scientific notation
    (sign, digits, exponent) = num.as_tuple()

    num_decimal_place = abs(exponent)
    num_formatted = format_precision_without_trailing_zero(num, places=num_decimal_place)
    if '.' not in num_formatted:
        return 0
    decimal_part = num_formatted.split('.')[1]
    num_decimal_zeros = 0
    for c in decimal_part:
        if c == '0':
            num_decimal_zeros += 1
        else:
            break
    return num_decimal_zeros

# Display fixed precision numbers exactly as it is, without converting to scientific format.
# 0.0100 -> 0.01
# 1.1000 -> 1.1
# 1.000 -> 1.000
def format_precision_without_trailing_zero(num: float, places: int) -> str:
    formatted_precision = f'{num:.{places}f}'
    dec = decimal.Decimal(formatted_precision).normalize()
    (sign, digits, exponent) = dec.as_tuple()
    return f'{num:.{abs(exponent)}f}'