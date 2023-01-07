import time
from contextlib import ContextDecorator


class TimeTrackerContext(ContextDecorator):
    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.start_time = time.time()
        print(f'Starting {self.name}')

    def __exit__(self, exc_type, exc_val, exc_tb):
        time_elapsed = time.time() - self.start_time
        print(f'Completed {self.name}. Time taken: {time_elapsed}')