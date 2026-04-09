from src.job.stocks.vix_central_message_sender import VixCentralMessageSender
from src.service.vix_central import RecentVixFuturesValues, VixFuturesValue
from src.util.my_telegram import escape_markdown


class TestVixCentralMessageSender:
    def test_format_vix_central_message_uses_increased_wording_for_positive_change(self):
        sender = VixCentralMessageSender()
        recent_values = RecentVixFuturesValues()

        current = VixFuturesValue(contango_single_day_decrease_alert_ratio=0.4)
        current.current_date = "2026-03-31"
        current.futures_date = "2026 Apr"
        current.formatted_contango = "-2.44%"
        current.raw_contango_change_prev_day = 0.6084
        current.formatted_contango_change_prev_day = "60.84%"
        current.is_contango_single_day_decrease_alert = False

        recent_values.vix_futures_values = [current]
        recent_values.is_contango_decrease_for_past_n_days = False

        message = sender._format_vix_central_message(recent_values)

        assert escape_markdown("increased") in message
        assert escape_markdown("60.84%") in message
        assert "changed by" not in message

    def test_format_vix_central_message_uses_unchanged_wording_for_zero_change(self):
        sender = VixCentralMessageSender()
        recent_values = RecentVixFuturesValues()

        current = VixFuturesValue(contango_single_day_decrease_alert_ratio=0.4)
        current.current_date = "2026-03-31"
        current.futures_date = "2026 Apr"
        current.formatted_contango = "5.00%"
        current.raw_contango_change_prev_day = 0.0
        current.formatted_contango_change_prev_day = "0.00%"
        current.is_contango_single_day_decrease_alert = False

        recent_values.vix_futures_values = [current]
        recent_values.is_contango_decrease_for_past_n_days = False

        message = sender._format_vix_central_message(recent_values)

        assert escape_markdown("unchanged from the previous day") in message
        assert escape_markdown("increased") not in message
        assert escape_markdown("decreased") not in message

    def test_format_vix_central_message_uses_decreased_wording_for_negative_change_alert(self):
        sender = VixCentralMessageSender()
        recent_values = RecentVixFuturesValues()

        current = VixFuturesValue(contango_single_day_decrease_alert_ratio=0.4)
        current.current_date = "2026-03-31"
        current.futures_date = "2026 Apr"
        current.formatted_contango = "-10.00%"
        current.raw_contango_change_prev_day = -1.0
        current.formatted_contango_change_prev_day = "-100.00%"
        current.is_contango_single_day_decrease_alert = True

        recent_values.vix_futures_values = [current]
        recent_values.is_contango_decrease_for_past_n_days = False

        message = sender._format_vix_central_message(recent_values)

        assert escape_markdown("decreased") in message
        assert escape_markdown("100.00%") in message
        assert escape_markdown("40.0%") in message
