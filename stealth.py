import random
import asyncio
import time
from collections import defaultdict
from fake_useragent import UserAgent

ua = UserAgent(browsers=["chrome", "firefox", "edge"])


class RateLimiter:
    def __init__(self, rate: float = 0.5, burst: int = 5):
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.updated_at = time.monotonic()

    async def acquire(self):
        while True:
            now = time.monotonic()
            elapsed = now - self.updated_at
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.updated_at = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            await asyncio.sleep(0.1)


class ProxyRotator:
    def __init__(self, proxies: list[str] | None = None):
        self._proxies = proxies or []
        self._index = 0

    def next(self) -> str | None:
        if not self._proxies:
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        if random.random() < 0.2 and len(self._proxies) > 1:
            self._index += random.randint(0, 1)
        return proxy

    @property
    def has_proxies(self) -> bool:
        return len(self._proxies) > 0


class UserAgentRotator:
    @staticmethod
    def next() -> str:
        brand = random.choice(["chrome", "firefox", "edge"])
        return ua[brand].random

    @staticmethod
    def random_accept() -> str:
        return random.choice([
            "application/json, text/plain, */*",
            "application/json, text/html, */*",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        ])

    @staticmethod
    def random_languages() -> str:
        return random.choice([
            "en-GB,en;q=0.9",
            "en-US,en;q=0.9",
            "en-GB,en-US;q=0.9,en;q=0.8",
            "en;q=0.9",
        ])


class TimingJitter:
    @staticmethod
    async def wait(min_ms: int = 500, max_ms: int = 2000):
        await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    @staticmethod
    def jitter(base: float, variance: float = 0.3) -> float:
        return base * (1 + random.uniform(-variance, variance))


class CookieSessionManager:
    def __init__(self):
        self._sessions: dict[int, list[dict]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def add_session(self, user_id: int, cookie: str, info: dict):
        async with self._lock:
            self._sessions[user_id].append({
                "cookie": cookie,
                "info": info,
                "last_used": 0,
                "use_count": 0,
            })

    async def get_session(self, user_id: int) -> Optional[str]:
        async with self._lock:
            sessions = self._sessions.get(user_id, [])
            if not sessions:
                return None
            sessions.sort(key=lambda s: (s["last_used"], s["use_count"]))
            chosen = sessions[0]
            chosen["last_used"] = time.time()
            chosen["use_count"] += 1
            return chosen["cookie"]

    async def remove_session(self, user_id: int, cookie: str):
        async with self._lock:
            self._sessions[user_id] = [
                s for s in self._sessions.get(user_id, [])
                if s["cookie"] != cookie
            ]

    def count(self, user_id: int) -> int:
        return len(self._sessions.get(user_id, []))


proxy_rotator = ProxyRotator()
rate_limiter = RateLimiter()
session_manager = CookieSessionManager()
