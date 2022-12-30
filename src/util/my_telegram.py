import telegram

def escape_markdown(text: str, version=2):
    return telegram.helpers.escape_markdown(text=text, version=version)