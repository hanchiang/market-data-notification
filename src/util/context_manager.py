import logging
import time
from contextlib import ContextDecorator

logger = logging.getLogger('Time tracker context')
class TimeTrackerContext(ContextDecorator):
    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.start_time = time.time()
        logger.info(f'Starting {self.name}')

    def __exit__(self, exc_type, exc_val, exc_tb):
        time_elapsed = time.time() - self.start_time
        logger.info(f'Completed {self.name}. Time taken: {time_elapsed}')