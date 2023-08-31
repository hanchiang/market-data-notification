import traceback

from src.util.my_telegram import escape_markdown

def get_exception_message(exception: Exception, cls = None, should_escape_markdown = False):
    tb = traceback.format_exc()
    message = ''
    if cls is not None:
        message = f'{cls} '
    exp = str(exception) if not should_escape_markdown else escape_markdown(str(exception))
    trace = str(tb) if not should_escape_markdown else escape_markdown(str(tb))
    return f'{message}exception: {exp}, stack trace: {trace}'
