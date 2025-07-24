import threading
import time

class RateLimiter:
    def __init__(self, max_requests_per_minute):
        self.lock = threading.Lock()
        self.delay = 60 / max_requests_per_minute
        self.last_call = 0

    def wait(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            wait_time = max(0, self.delay - elapsed)
            if wait_time > 0:
                time.sleep(wait_time)
            self.last_call = time.time()
