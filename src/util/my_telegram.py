from typing import List

import telegram

def escape_markdown(text: str, version=2, should_escape=True):
    if not should_escape:
        return text
    return telegram.helpers.escape_markdown(text=text, version=version)

def format_messages_to_telegram(messages: List[str]) -> str:
    return escape_markdown(f"\n{message_separator()}\n").join(
        messages)
def message_separator() -> str:
    return '-----------------------------------------------------------------'

def exclamation_mark() -> str:
    return '❗'