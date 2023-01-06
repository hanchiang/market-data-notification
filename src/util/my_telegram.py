import telegram

def escape_markdown(text: str, version=2):
    return telegram.helpers.escape_markdown(text=text, version=version)

def format_messages_to_telegram(messages: list[str]) -> str:
    return escape_markdown("\n-----------------------------------------------------------------\n").join(
        messages)