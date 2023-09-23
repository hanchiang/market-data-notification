from src.type.metric_type import MetricTypeIndicator
from src.util.my_telegram import exclamation_mark


def bold_text_for_metric_type(metric_type: MetricTypeIndicator, value: float) -> str:
    thresholds = metric_type.value[1:]
    res = ''
    for threshold in thresholds:
        if abs(value) < threshold:
            break
        res = f'{res}{exclamation_mark()}'
    return res
