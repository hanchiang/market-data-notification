import asyncio

from src import server
from src.telegram_app import start_telegram_app

if __name__ == '__main__':
  loop = asyncio.new_event_loop()
  asyncio.set_event_loop(loop)
  loop.create_task(start_telegram_app())
  server.start_server(loop)
