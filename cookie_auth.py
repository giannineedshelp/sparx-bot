import asyncio
import aiohttp
import random
import re
import json
from typing import Optional

from stealth import proxy_rotator, TimingJitter


class SparxCookieAcquirer:
    """
    Logs into Sparx Maths using email/password and extracts session cookies.
    No browser extension needed — runs entirely server-side.
    """

    AUTH_URL = "https://auth.sparxmaths.uk/oauth2/auth"
    TOKEN_URL = "https://studentapi.api.sparxmaths.uk/oauth/token"
    LOGIN_URL = "https://auth.sparxmaths.uk/login"
    CALLBACK_URL = "https://studentapi.api.sparxmaths.uk/oauth/callback"

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        jar = aiohttp.CookieJar()
        proxy = proxy_rotator.next()
        connector_args = {}
        if proxy:
            if proxy.startswith("socks"):
                from aiohttp_socks import ProxyConnector
                connector_args["connector"] = ProxyConnector.from_url(f"socks5://{proxy}")
            else:
                connector_args["connector"] = aiohttp.TCPConnector()

        self._session = aiohttp.ClientSession(
            cookie_jar=jar,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
                "Origin": "https://auth.sparxmaths.uk",
                "Referer": "https://auth.sparxmaths.uk/",
            },
            **connector_args
        )
        return self

    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()

    async def _get_init_state(self) -> dict:
        """Step 1: Get the initial auth page and extract state/redirect params."""
        params = {
            "client_id": "sparx-maths-sw",
            "hd": "fe264163-799c-460e-bf73-0cca187a378e",
            "redirect_uri": self.CALLBACK_URL,
            "response_type": "code",
            "scope": "openid profile email",
        }
        async with self._session.get(self.AUTH_URL, params=params, allow_redirects=True) as resp:
            html = await resp.text()

        # Extract state from the form or URL
        state_match = re.search(r'name="state"\s+value="([^"]+)"', html)
        state = state_match.group(1) if state_match else None

        # Also check for existing login (maybe already authenticated)
        if "login" not in resp.url.path and not state:
            # Might already be logged in — check cookies
            cookies = self._session.cookie_jar.filter_cookies(resp.url)
            cookie_str = "; ".join(f"{c.key}={c.value}" for c in cookies.values())
            if cookie_str:
                return {"state": None, "cookies": cookie_str, "already_logged_in": True}

        return {"state": state, "cookies": None, "already_logged_in": False}

    async def _get_login_page(self, state: str) -> str:
        """Step 2: Get the actual login page with CSRF tokens."""
        params = {
            "client_id": "sparx-maths-sw",
            "hd": "fe264163-799c-460e-bf73-0cca187a378e",
            "redirect_uri": self.CALLBACK_URL,
            "response_type": "code",
            "scope": "openid profile email",
            "state": state,
        }
        async with self._session.get(self.AUTH_URL, params=params, allow_redirects=True) as resp:
            return await resp.text()

    async def _extract_csrf(self, html: str) -> Optional[str]:
        """Extract CSRF token from login page."""
        # Try multiple patterns
        patterns = [
            r'name="csrf_token"\s+value="([^"]+)"',
            r'name="_csrf"\s+value="([^"]+)"',
            r'name="authenticity_token"\s+value="([^"]+)"',
            r'<input[^>]*csrf[^>]*value="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    async def _login_post(
        self, email: str, password: str, csrf: Optional[str], state: str
    ) -> tuple[bool, str]:
        """Step 3: Submit the login form."""
        data = {
            "username": email,
            "password": password,
        }
        if csrf:
            data["csrf_token"] = csrf
        if state:
            data["state"] = state

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://auth.sparxmaths.uk",
            "Referer": "https://auth.sparxmaths.uk/login",
        }

        async with self._session.post(
            self.LOGIN_URL,
            data=data,
            headers=headers,
            allow_redirects=True
        ) as resp:
            # Check if login succeeded
            final_url = str(resp.url)
            body = await resp.text()

            if "login" in final_url and "error" in final_url:
                error_match = re.search(r'error[^"]*["\']?\s*[:=]\s*["\']([^"\']+)', body, re.IGNORECASE)
                error = error_match.group(1) if error_match else "Unknown login error"
                return False, error

            if "login" not in final_url and "callback" in final_url:
                # Success — cookies should be set
                cookies = self._session.cookie_jar.filter_cookies(resp.url)
                cookie_str = "; ".join(f"{c.key}={c.value}" for c in cookies.values())
                if cookie_str:
                    return True, cookie_str

            # Try to get cookies anyway
            cookies = self._session.cookie_jar.filter_cookies(resp.url)
            cookie_str = "; ".join(f"{c.key}={c.value}" for c in cookies.values())
            if cookie_str and len(cookie_str) > 20:
                return True, cookie_str

            return False, "Login failed — check credentials."

    async def _exchange_code_for_cookies(self, code: str) -> Optional[str]:
        """Step 4: Exchange authorization code for session cookies."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.CALLBACK_URL,
            "client_id": "sparx-maths-sw",
        }
        async with self._session.post(self.TOKEN_URL, data=data) as resp:
            if resp.status == 200:
                cookies = self._session.cookie_jar.filter_cookies(resp.url)
                cookie_str = "; ".join(f"{c.key}={c.value}" for c in cookies.values())
                if cookie_str:
                    return cookie_str
            return None

    async def acquire(self, email: str, password: str) -> tuple[bool, str]:
        """
        Main entry point — logs in and returns (success, cookie_string_or_error).
        """
        await TimingJitter.wait(2000, 5000)

        try:
            # Step 1: Get initial state
            result = await self._get_init_state()
            if result.get("already_logged_in"):
                return True, result["cookies"]

            state = result.get("state")
            if not state:
                return False, "Could not initialise login."

            # Step 2: Get login page
            login_html = await self._get_login_page(state)
            csrf = await self._extract_csrf(login_html)

            # Step 3: Submit credentials
            await TimingJitter.wait(1000, 3000)
            success, result_str = await self._login_post(email, password, csrf, state)

            if success:
                # Validate the cookies work
                test_client = aiohttp.ClientSession(
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Cookie": result_str,
                    }
                )
                try:
                    async with test_client.get(
                        "https://studentapi.api.sparxmaths.uk/user/info"
                    ) as test_resp:
                        if test_resp.status == 200:
                            return True, result_str
                        else:
                            return False, "Cookies acquired but session is invalid."
                finally:
                    await test_client.close()

                return True, result_str
            else:
                return False, result_str

        except Exception as e:
            return False, f"Login error: {str(e)}"

    async def validate_cookies(self, cookie_str: str) -> bool:
        """Quick validation of a cookie string against the Sparx API."""
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://studentapi.api.sparxmaths.uk/user/info",
                headers={"Cookie": cookie_str}
            ) as resp:
                return resp.status == 200
