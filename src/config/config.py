import os
from dotenv import load_dotenv

load_dotenv()

def get_telegram_bot_token():
    if not os.getenv('TELEGRAM_BOT_TOKEN', None):
        raise RuntimeError("telegram bot token is missing")
    return os.getenv('TELEGRAM_BOT_TOKEN')

def get_telegram_admin_bot_token():
    if not os.getenv('TELEGRAM_ADMIN_BOT_TOKEN', None):
        raise RuntimeError("telegram admin bot token is missing")
    return os.getenv('TELEGRAM_ADMIN_BOT_TOKEN')

def get_telegram_channel_id():
    if not os.getenv('TELEGRAM_CHANNEL_ID', None):
        raise RuntimeError("telegram channel is missing")
    return os.getenv('TELEGRAM_CHANNEL_ID')

def get_is_testing_telegram():
    return os.getenv('IS_TESTING_TELEGRAM', False) == 'true'

def get_simulate_real_traffic():
    return os.getenv('SIMULATE_REAL_TRAFFIC', False) == 'true'

def get_trading_view_ips():
    if not os.getenv('TRADING_VIEW_IPS', None):
        raise RuntimeError("trading view ips is missing")
    return os.getenv('TRADING_VIEW_IPS').split(',')

def get_telegram_admin_id():
    if not os.getenv('TELEGRAM_ADMIN_ID', None):
        raise RuntimeError("telegram admin id is missing")
    return os.getenv('TELEGRAM_ADMIN_ID')

def get_contango_single_day_decrease_threshold_ratio():
    return 0.4 if not get_is_testing_telegram() else 0.01

def get_contango_decrease_past_n_days_threshold():
    return 5 if not get_is_testing_telegram() else 2

def get_vix_central_number_of_days():
    return 5

def get_potential_overextended_by_symbol():
    potential_overextended_by_symbol = {
        'SPY': {
            'up': 0.04 if not get_is_testing_telegram() else 0.01,
            'down': -0.065 if not get_is_testing_telegram() else -0.01
        }
    }
    return potential_overextended_by_symbol