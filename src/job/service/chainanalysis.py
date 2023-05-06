from src.util.my_telegram import escape_markdown

def format_thirdparty_chainanalysis_message(res: dict):
    message = f"*{escape_markdown(res.get('highlight', ''))}*\n"
    return message