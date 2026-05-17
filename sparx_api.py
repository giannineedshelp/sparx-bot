import aiohttp
import random
import asyncio
from typing import Optional

from stealth import (
    proxy_rotator, rate_limiter, UserAgentRotator,
    TimingJitter, session_manager
)


class SparxAPIError(Exception):
    pass


class SparxAPIClient:
    BASE_URL = "https://studentapi.api.sparxmaths.uk"

    def __init__(self, cookie_string: Optional[str] = None, user_id: Optional[int] = None):
        self._cookie_string = cookie_string
        self._user_id = user_id
        self._session: Optional[aiohttp.ClientSession] = None
        self._user_agent = UserAgentRotator.next()

    async def _resolve_cookie(self) -> str:
        if self._cookie_string:
            return self._cookie_string
        if self._user_id:
            cookie = await session_manager.get_session(self._user_id)
            if cookie:
                return cookie
            from storage import load_session
            data = load_session(self._user_id)
            if data and data.get("accounts"):
                acct = random.choice(data["accounts"])
                await session_manager.add_session(
                    self._user_id, acct["cookie"], acct["info"]
                )
                return acct["cookie"]
        raise SparxAPIError("No session available. Use `/login` first.")

    async def __aenter__(self):
        cookie_str = await self._resolve_cookie()
        proxy = proxy_rotator.next()

        connector_args = {}
        if proxy:
            if proxy.startswith("socks"):
                from aiohttp_socks import ProxyConnector
                connector_args["connector"] = ProxyConnector.from_url(f"socks5://{proxy}")
            else:
                connector_args["connector"] = aiohttp.TCPConnector()

        timeout = aiohttp.ClientTimeout(total=30)
        jar = aiohttp.CookieJar()

        self._session = aiohttp.ClientSession(
            base_url=self.BASE_URL,
            cookie_jar=jar,
            headers={
                "User-Agent": self._user_agent,
                "Accept": UserAgentRotator.random_accept(),
                "Accept-Language": UserAgentRotator.random_languages(),
                "Accept-Encoding": "gzip, deflate, br",
                "Origin": "https://www.sparxmaths.uk",
                "Referer": "https://www.sparxmaths.uk/",
                "Sec-Fetch-Site": "same-site",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "Sec-Ch-Ua": self._build_sec_ch_ua(),
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": random.choice(['"Windows"', '"macOS"', '"Linux"']),
                "DNT": "1",
                "Connection": "keep-alive",
            },
            timeout=timeout,
            **connector_args
        )

        for c in self._parse_cookies(cookie_str):
            self._session.cookie_jar.update_cookies(
                {c["name"]: c["value"]},
                aiohttp.URL("https://www.sparxmaths.uk")
            )

        return self

    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()

    @staticmethod
    def _build_sec_ch_ua() -> str:
        v = random.choice(["124", "125", "126", "127"])
        return f'"Chromium";v="{v}", "Google Chrome";v="{v}", "Not=A?Brand";v="{random.choice(["8", "99", "24"])}"'

    @staticmethod
    def _parse_cookies(cookie_str: str) -> list[dict]:
        cookies = []
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                n, v = part.split("=", 1)
                cookies.append({"name": n.strip(), "value": v.strip()})
        return cookies

    async def _request(self, method: str, path: str, **kwargs) -> dict | list:
        if not self._session:
            raise SparxAPIError("Not connected.")

        await rate_limiter.acquire()
        await TimingJitter.wait(300, 1000)

        url = f"{self.BASE_URL}{path}"
        kwargs.setdefault("headers", {})
        kwargs["headers"]["Cookie"] = await self._resolve_cookie()
        kwargs.setdefault("ssl", True)

        async with self._session.request(method, url, **kwargs) as resp:
            if resp.status == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                wait_time = retry_after + random.uniform(1, 3)
                await asyncio.sleep(wait_time)
                async with self._session.request(method, url, **kwargs) as retry_resp:
                    if retry_resp.status != 200:
                        text = await retry_resp.text()
                        raise SparxAPIError(f"Rate limited. Try again later. ({retry_resp.status})")
                    return await retry_resp.json()
            if resp.status == 401:
                if self._user_id:
                    from storage import load_session
                    data = load_session(self._user_id)
                    if data and len(data.get("accounts", [])) > 1:
                        await session_manager.remove_session(
                            self._user_id, await self._resolve_cookie()
                        )
                        new_cookie = await session_manager.get_session(self._user_id)
                        if new_cookie:
                            kwargs["headers"]["Cookie"] = new_cookie
                            async with self._session.request(method, url, **kwargs) as retry:
                                if retry.status == 200:
                                    return await retry.json()
                raise SparxAPIError("Session expired. Use `/login` to re-link your account.")
            if resp.status != 200:
                text = await resp.text()
                raise SparxAPIError(f"Sparx API returned status {resp.status}.")
            return await resp.json()

    async def validate(self) -> bool:
        try:
            await self._request("GET", "/user/info")
            return True
        except Exception:
            return False

    async def get_user_info(self) -> dict:
        return await self._request("GET", "/user/info")

    async def get_homework(self) -> list:
        data = await self._request("GET", "/homework")
        return data if isinstance(data, list) else data.get("homework", [])

    async def get_homework_tasks(self, hw_id: str) -> list:
        data = await self._request("GET", f"/homework/{hw_id}/tasks")
        return data if isinstance(data, list) else data.get("tasks", [])

    async def get_task_details(self, task_id: str) -> dict:
        return await self._request("GET", f"/task/{task_id}")

    async def get_bookwork_codes(self, hw_id: str) -> list:
        data = await self._request("GET", f"/homework/{hw_id}/bookwork")
        return data if isinstance(data, list) else data.get("bookwork_codes", [])

    async def submit_bookwork_evidence(self, task_id: str, code: str, answers: list[str]) -> dict:
        return await self._request(
            "POST", f"/task/{task_id}/bookwork/verify",
            json={"bookwork_code": code, "answers": answers}
        )

    async def submit_answer(self, task_id: str, q_id: str, answer_data: dict) -> dict:
        return await self._request(
            "POST", f"/task/{task_id}/question/{q_id}/answer",
            json=answer_data
        )
