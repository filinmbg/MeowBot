import asyncio
import logging

# === Налаштування кількості одночасних потоків
MAX_CONCURRENT_TASKS = 1  # ← ЗМІНЮЙ ЦЕ ЗНАЧЕННЯ

# === Логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Semaphore")

class GlobalSemaphore:
    """
    Обмежувач кількості одночасних потоків.
    """
    def __init__(self, max_concurrent: int = MAX_CONCURRENT_TASKS):
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        logger.info(f"🔐 Semaphore ініціалізовано: дозволено одночасно {self.max_concurrent} потоків")

    async def run(self, coro):
        async with self._semaphore:
            return await coro
