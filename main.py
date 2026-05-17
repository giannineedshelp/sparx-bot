import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import random
from typing import Optional

from sparx_api import SparxAPIClient, SparxAPIError
from storage import (
    save_session, load_session, delete_session,
    remove_account, list_all_users, get_account_count,
    save_bookwork_temp, get_bookwork_temp,
    get_all_bookwork_temp, clear_bookwork_temp,
    has_bookwork_temp, count_bookwork_temp
)
from stealth import proxy_rotator, session_manager, TimingJitter
from cookie_auth import SparxCookieAcquirer
from config import BOT_TOKEN, ALT_TOKENS, GUILD_ID, OWNER_ID, PROXY_LIST, MAX_ALT_ACCOUNTS

# ── Colours ──────────────────────────────────

BLURPLE = 0x5865F2
GREEN = 0x57F287
YELLOW = 0xFEE75C
RED = 0xED4245

# ── Alt bots ─────────────────────────────────

alt_clusters: list[commands.Bot] = []

async def start_alt_bots():
    for i, token in enumerate(ALT_TOKENS[:MAX_ALT_ACCOUNTS]):
        alt_bot = commands.Bot(
            command_prefix=None,
            intents=discord.Intents.default(),
            help_command=None
        )
        @alt_bot.event
        async def on_ready():
            print(f"   Alt {i+1} ready: {alt_bot.user}")
        try:
            asyncio.create_task(alt_bot.start(token, reconnect=True))
            alt_clusters.append(alt_bot)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"   Alt {i+1} failed: {e}")
    print(f"  {len(alt_clusters)} alt bots running.")

# ── Helper ───────────────────────────────────

def embed(title: str, desc: str = "", color=BLURPLE) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=color)

# ── Cog ──────────────────────────────────────

class CommandCentre(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ══════════════════════════════════════════
    #  DASHBOARD
    # ══════════════════════════════════════════

    @app_commands.command(name="dashboard", description="Open the Sparx command centre")
    async def dashboard(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = load_session(interaction.user.id)
        accounts = data.get("accounts", []) if data else []
        bwc = count_bookwork_temp(interaction.user.id)

        e = embed("\U0001f4a0 Sparx Command Centre", "All your Sparx Maths tools in one place.", BLURPLE)
        e.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

        if accounts:
            names = "\n".join(
                f"  {i+1}. `{a['info'].get('displayName') or a['info'].get('email', '?')}`"
                for i, a in enumerate(accounts)
            )
            e.add_field(name=f"\u2705 Connected ({len(accounts)}/3)", value=f"{names}\n\nBookwork cached: **{bwc}** codes", inline=False)
            view = MainMenu(logged_in=True, has_bookwork=bool(bwc))
        else:
            e.add_field(name="\u274c Not logged in", value="Use `/login email password` or `/logincookies <cookies>` to get started.", inline=False)
            view = MainMenu(logged_in=False, has_bookwork=False)

        proxy_status = f"`{len(PROXY_LIST)}`" if PROXY_LIST else "`None`"
        e.set_footer(text=f"Proxies: {proxy_status} | Alts: {len(alt_clusters)}")
        await interaction.followup.send(embed=e, view=view, ephemeral=True)

    # ══════════════════════════════════════════
    #  LOGIN VIA EMAIL/PASSWORD  (NEW — no extension needed)
    # ══════════════════════════════════════════

    @app_commands.command(name="login", description="Log into Sparx with email & password (no extension needed)")
    @app_commands.describe(email="Your Sparx Maths email/username", password="Your Sparx Maths password")
    async def login(self, interaction: discord.Interaction, email: str, password: str):
        await interaction.response.defer(ephemeral=True)

        if get_account_count(interaction.user.id) >= 3:
            return await interaction.followup.send(
                embed=embed("\u274c Limit Reached", "You already have 3 accounts. Use `/removeaccount <email>` first.", RED),
                ephemeral=True
            )

        # Tell the user we're logging in
        await interaction.followup.send("\u23f3 Logging into Sparx Maths... (this takes a few seconds)", ephemeral=True)

        # Acquire cookies server-side
        acquirer = SparxCookieAcquirer()
        async with acquirer as aq:
            success, result = await aq.acquire(email, password)

        if not success:
            return await interaction.edit_original_response(
                content=None,
                embed=embed("\u274c Login failed", f"**Reason:** {result}\n\nMake sure your email and password are correct.\n\nStill having trouble? Use `/logincookies <cookies>` instead — paste cookies from a desktop browser where you're already logged in.", RED)
            )

        cookie_str = result

        # Validate and get user info
        client = SparxAPIClient(cookie_str)
        try:
            async with client as c:
                info = await c.get_user_info()
        except SparxAPIError as e:
            return await interaction.edit_original_response(
                content=None,
                embed=embed("\u274c Session invalid", f"Cookies were obtained but the session is invalid. Try `/logincookies <cookies>` instead.\n\nAPI error: {e}", RED)
            )

        # Save
        if not save_session(interaction.user.id, cookie_str, info):
            return await interaction.edit_original_response(
                content=None,
                embed=embed("\u274c Limit Reached", "You already have 3 accounts.", RED)
            )
        await session_manager.add_session(interaction.user.id, cookie_str, info)

        name = info.get("displayName") or info.get("email") or "Unknown"
        now = get_account_count(interaction.user.id)
        await interaction.edit_original_response(
            content=None,
            embed=embed("\u2705 Logged in!", f"**{name}**\nAccounts: **{now}/3**\n\n\U0001f447 Open the dashboard to get started.", GREEN),
            view=QuickDash()
        )

    # ══════════════════════════════════════════
    #  LOGIN VIA COOKIES  (fallback for desktop users)
    # ══════════════════════════════════════════

    @app_commands.command(name="logincookies", description="Link Sparx via cookie string (from browser extension)")
    @app_commands.describe(cookies="Paste the cookie string from the Cookie Getter extension")
    async def logincookies(self, interaction: discord.Interaction, cookies: str):
        await interaction.response.defer(ephemeral=True)

        if get_account_count(interaction.user.id) >= 3:
            return await interaction.followup.send(
                embed=embed("\u274c Limit Reached", "You already have 3 accounts.", RED),
                ephemeral=True
            )

        client = SparxAPIClient(cookies.strip())
        try:
            async with client as c:
                if not await c.validate():
                    return await interaction.followup.send("\u274c Invalid cookies.", ephemeral=True)
                info = await c.get_user_info()
        except SparxAPIError as e:
            return await interaction.followup.send(f"\u274c {e}", ephemeral=True)

        if not save_session(interaction.user.id, cookies.strip(), info):
            return await interaction.followup.send(embed=embed("\u274c Limit", "Max 3 accounts.", RED), ephemeral=True)
        await session_manager.add_session(interaction.user.id, cookies.strip(), info)

        name = info.get("displayName") or info.get("email") or "Unknown"
        now = get_account_count(interaction.user.id)
        await interaction.followup.send(
            embed=embed("\u2705 Linked!", f"**{name}** — Accounts: **{now}/3**", GREEN),
            view=QuickDash(),
            ephemeral=True
        )

    # ══════════════════════════════════════════
    #  ADD ACCOUNT VIA EMAIL/PASSWORD
    # ══════════════════════════════════════════

    @app_commands.command(name="addaccount", description="Add another Sparx account via email/password")
    @app_commands.describe(email="Sparx Maths email", password="Sparx Maths password")
    async def addaccount(self, interaction: discord.Interaction, email: str, password: str):
        await interaction.response.defer(ephemeral=True)

        if get_account_count(interaction.user.id) >= 3:
            return await interaction.followup.send(embed=embed("\u274c Limit", "Max 3 accounts.", RED), ephemeral=True)

        await interaction.followup.send("\u23f3 Logging in...", ephemeral=True)

        acquirer = SparxCookieAcquirer()
        async with acquirer as aq:
            success, result = await aq.acquire(email, password)

        if not success:
            return await interaction.edit_original_response(
                content=None,
                embed=embed("\u274c Login failed", result, RED)
            )

        client = SparxAPIClient(result)
        try:
            async with client as c:
                info = await c.get_user_info()
        except SparxAPIError as e:
            return await interaction.edit_original_response(
                content=None,
                embed=embed("\u274c Error", str(e), RED)
            )

        if not save_session(interaction.user.id, result, info):
            return await interaction.edit_original_response(
                content=None,
                embed=embed("\u274c Limit", "Max 3 accounts.", RED)
            )
        await session_manager.add_session(interaction.user.id, result, info)

        await interaction.edit_original_response(
            content=None,
            embed=embed("\u2705 Added", f"Linked `{info.get('email', '?')}` — {get_account_count(interaction.user.id)}/3", GREEN)
        )

    # ══════════════════════════════════════════
    #  ADD ACCOUNT VIA COOKIES
    # ══════════════════════════════════════════

    @app_commands.command(name="addaccountcookies", description="Add another account via cookie string")
    @app_commands.describe(cookies="Cookie string from another Sparx account")
    async def addaccountcookies(self, interaction: discord.Interaction, cookies: str):
        await interaction.response.defer(ephemeral=True)
        if get_account_count(interaction.user.id) >= 3:
            return await interaction.followup.send(embed=embed("\u274c Limit", "Max 3.", RED), ephemeral=True)
        client = SparxAPIClient(cookies.strip())
        try:
            async with client as c:
                if not await c.validate():
                    return await interaction.followup.send("\u274c Invalid cookies.", ephemeral=True)
                info = await c.get_user_info()
        except SparxAPIError as e:
            return await interaction.followup.send(f"\u274c {e}", ephemeral=True)
        if not save_session(interaction.user.id, cookies.strip(), info):
            return await interaction.followup.send(embed=embed("\u274c Limit", "Max 3.", RED), ephemeral=True)
        await session_manager.add_session(interaction.user.id, cookies.strip(), info)
        await interaction.followup.send(embed=embed("\u2705 Added", f"Accounts: {get_account_count(interaction.user.id)}/3", GREEN), ephemeral=True)

    # ══════════════════════════════════════════
    #  ACCOUNTS
    # ══════════════════════════════════════════

    @app_commands.command(name="accounts", description="View linked accounts")
    async def accounts(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = load_session(interaction.user.id)
        if not data or not data.get("accounts"):
            return await interaction.followup.send(embed=embed("\u274c No accounts", "Use `/login` first.", YELLOW), ephemeral=True)
        accts = data["accounts"]
        e = embed("\U0001f465 Linked Accounts", f"**{len(accts)}/3** — rotating for stealth", BLURPLE)
        for i, a in enumerate(accts):
            inf = a.get("info", {})
            e.add_field(
                name=f"Account {i+1}",
                value=f"Name: `{inf.get('displayName', '?')}`\nSchool: `{inf.get('school', '?')}`\nLinked: <t:{a.get('saved_at', 0)}:R>",
                inline=True
            )
        await interaction.followup.send(embed=e, view=BackToDash(), ephemeral=True)

    # ══════════════════════════════════════════
    #  REMOVE ACCOUNT
    # ══════════════════════════════════════════

    @app_commands.command(name="removeaccount", description="Remove an account by email")
    @app_commands.describe(email="The email of the account to remove")
    async def removeaccount(self, interaction: discord.Interaction, email: str):
        if remove_account(interaction.user.id, email):
            await interaction.response.send_message(embed=embed("\u2705 Removed", f"Accounts: {get_account_count(interaction.user.id)}/3", GREEN), ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed("\u274c Not found", "Check `/accounts` for emails.", RED), ephemeral=True)

    # ══════════════════════════════════════════
    #  PROFILE
    # ══════════════════════════════════════════

    @app_commands.command(name="profile", description="View your Sparx profile")
    async def profile(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        client = SparxAPIClient(user_id=interaction.user.id)
        try:
            async with client as c:
                info = await c.get_user_info()
        except SparxAPIError as e:
            return await interaction.followup.send(embed=embed("\u274c Error", str(e), RED), ephemeral=True)
        e = embed("\U0001f464 Sparx Profile", "", BLURPLE)
        e.add_field(name="Name", value=info.get("displayName", "N/A"), inline=True)
        e.add_field(name="Email", value=info.get("email", "N/A"), inline=True)
        e.add_field(name="School", value=info.get("school", "N/A"), inline=True)
        e.add_field(name="Year", value=info.get("yearGroup", "N/A"), inline=True)
        await interaction.followup.send(embed=e, view=BackToDash(), ephemeral=True)

    # ══════════════════════════════════════════
    #  HOMEWORK
    # ══════════════════════════════════════════

    @app_commands.command(name="homework", description="View homework assignments")
    async def homework(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        client = SparxAPIClient(user_id=interaction.user.id)
        try:
            async with client as c:
                hw_list = await c.get_homework()
        except SparxAPIError as e:
            return await interaction.followup.send(embed=embed("\u274c Error", str(e), RED), ephemeral=True)

        if not hw_list:
            return await interaction.followup.send(embed=embed("\U0001f4ed No homework", "Nothing due right now!", GREEN), ephemeral=True)

        e = embed("\U0001f4da Sparx Homework", f"**{len(hw_list)}** assignment{'s' if len(hw_list) > 1 else ''}", BLURPLE)
        for hw in hw_list[:8]:
            title = hw.get("title", "Untitled")
            due = hw.get("dueDate", "N/A")
            status = hw.get("status", "unknown")
            hid = hw.get("id", "???")
            emoji = "\u2705" if status == "completed" else "\u23f3"
            e.add_field(name=f"{emoji} {title}", value=f"Due: `{due}` | ID: `{hid}`", inline=False)

        await interaction.followup.send(embed=e, view=HomeworkView(hw_list[:8], interaction.user.id), ephemeral=True)

    # ══════════════════════════════════════════
    #  TASKS
    # ══════════════════════════════════════════

    async def show_tasks(self, interaction: discord.Interaction, homework_id: str):
        await interaction.response.defer(ephemeral=True)
        client = SparxAPIClient(user_id=interaction.user.id)
        try:
            async with client as c:
                tasks_list = await c.get_homework_tasks(homework_id)
        except SparxAPIError as e:
            return await interaction.followup.send(embed=embed("\u274c Error", str(e), RED), ephemeral=True)

        if not tasks_list:
            return await interaction.followup.send(embed=embed("\U0001f4ed No tasks", "No tasks for this homework.", YELLOW), ephemeral=True)

        e = embed(f"\U0001f4cb Tasks for `{homework_id[:8]}...`", f"**{len(tasks_list)}** task{'s' if len(tasks_list) > 1 else ''}", BLURPLE)
        for t in tasks_list[:10]:
            title = t.get("title", "Untitled")
            status = t.get("status", "unknown")
            tid = t.get("id", "???")
            emoji = "\u2705" if status == "completed" else "\u23f3"
            e.add_field(name=f"{emoji} {title}", value=f"Status: `{status}` | ID: `{tid}`", inline=False)

        await interaction.followup.send(embed=e, view=TasksView(tasks_list[:10], homework_id, interaction.user.id), ephemeral=True)

    @app_commands.command(name="tasks", description="View tasks for a homework")
    @app_commands.describe(homework_id="Homework ID from /homework")
    async def tasks_cmd(self, interaction: discord.Interaction, homework_id: str):
        await self.show_tasks(interaction, homework_id)

    # ══════════════════════════════════════════
    #  AUTO-BOOKWORK
    # ══════════════════════════════════════════

    async def run_autobookwork(self, interaction: discord.Interaction, homework_id: str):
        await interaction.response.defer(ephemeral=True)
        client = SparxAPIClient(user_id=interaction.user.id)
        await interaction.followup.send("\u23f3 Running autobookwork...", ephemeral=True)

        async with client as c:
            try:
                codes = await c.get_bookwork_codes(homework_id)
            except SparxAPIError as e:
                return await interaction.edit_original_response(content=None, embed=embed("\u274c Error", str(e), RED))

            if not codes:
                return await interaction.edit_original_response(content=None, embed=embed("\U0001f4ed No codes", "No bookwork codes found.", YELLOW))

            results = []
            new_cached = 0
            for entry in codes[:15]:
                code = entry.get("code", "???")
                task_id = entry.get("task_id", "")
                cached = get_bookwork_temp(interaction.user.id, code)
                if cached:
                    answers = cached["answers"]
                else:
                    try:
                        details = await c.get_task_details(task_id)
                        questions = details.get("questions", [])
                        answers = [q.get("answer") for q in questions if q.get("answer")]
                        if answers:
                            save_bookwork_temp(interaction.user.id, code, task_id, answers)
                            new_cached += 1
                    except SparxAPIError:
                        results.append(f"\u274c `{code}` (fetch failed)")
                        await TimingJitter.wait(1500, 4000)
                        continue
                try:
                    if answers:
                        await c.submit_bookwork_evidence(task_id, code, answers)
                        results.append(f"\u2705 `{code}`")
                    else:
                        results.append(f"\u26a0 `{code}` (no answers)")
                except SparxAPIError as e:
                    results.append(f"\u274c `{code}` ({e})")
                await TimingJitter.wait(1500, 4000)

        total = count_bookwork_temp(interaction.user.id)
        e = embed("\U0001f4d6 Bookwork Results", "\n".join(results) if results else "Nothing processed.", GREEN)
        e.set_footer(text=f"{new_cached} new cached | {total} total in storage")
        await interaction.edit_original_response(content=None, embed=e, view=BackToDash())

    @app_commands.command(name="autobookwork", description="Auto-submit bookwork for a homework")
    @app_commands.describe(homework_id="Homework ID")
    async def autobookwork_cmd(self, interaction: discord.Interaction, homework_id: str):
        await self.run_autobookwork(interaction, homework_id)

    # ══════════════════════════════════════════
    #  BOOKWORK  (browse + submit individual codes)
    # ══════════════════════════════════════════

    @app_commands.command(name="bookwork", description="Browse cached bookwork codes")
    async def bookwork_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._show_bookwork_page(interaction, 0)

    async def _show_bookwork_page(self, interaction: discord.Interaction, page: int):
        codes = get_all_bookwork_temp(interaction.user.id)
        if not codes:
            return await interaction.followup.send(
                embed=embed("\U0001f4ed Empty", "No bookwork codes cached yet.\nUse `/autobookwork <hw_id>` first.", YELLOW),
                ephemeral=True
            )

        sorted_codes = sorted(codes.items(), key=lambda x: x[1]["captured_at"], reverse=True)
        items_per_page = 5
        total_pages = max(1, (len(sorted_codes) + items_per_page - 1) // items_per_page)
        page = max(0, min(page, total_pages - 1))
        start = page * items_per_page
        end = start + items_per_page
        page_items = sorted_codes[start:end]

        e = embed(
            "\U0001f4d6 Bookwork Codes",
            f"Page **{page+1}/{total_pages}** — **{len(sorted_codes)}** codes cached (expire 1hr)",
            BLURPLE
        )
        for code, data in page_items:
            answers = data.get("answers", [])
            answers_str = ", ".join(answers[:3])
            if len(answers) > 3:
                answers_str += f" ... (+{len(answers)-3})"
            e.add_field(
                name=f"`{code}`",
                value=f"Answers: `{answers_str}`\nCaptured: <t:{data['captured_at']}:R>",
                inline=False
            )

        await interaction.edit_original_response(embed=e, view=BookworkBrowseView(interaction.user.id, page, total_pages, page_items))

    async def submit_single_code(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer(ephemeral=True)
        data = get_bookwork_temp(interaction.user.id, code)
        if not data:
            return await interaction.followup.send(embed=embed("\u274c Not found", "That code expired or doesn't exist.", RED), ephemeral=True)
        client = SparxAPIClient(user_id=interaction.user.id)
        async with client as c:
            try:
                await c.submit_bookwork_evidence(data["task_id"], code, data["answers"])
                await interaction.followup.send(embed=embed(f"\u2705 `{code}` submitted!", f"Answers: `{', '.join(data['answers'][:5])}`", GREEN), ephemeral=True)
            except SparxAPIError as e:
                await interaction.followup.send(embed=embed(f"\u274c `{code}` failed", str(e), RED), ephemeral=True)

    async def replay_all_bookwork(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        codes = get_all_bookwork_temp(interaction.user.id)
        if not codes:
            return await interaction.followup.send(embed=embed("\U0001f4ed Empty", "Nothing to replay.", YELLOW), ephemeral=True)
        await interaction.followup.send("\u23f3 Replaying all...", ephemeral=True)
        results = []
        client = SparxAPIClient(user_id=interaction.user.id)
        async with client as c:
            for code, data in codes.items():
                try:
                    await c.submit_bookwork_evidence(data["task_id"], code, data["answers"])
                    results.append(f"\u2705 `{code}`")
                except SparxAPIError as e:
                    results.append(f"\u274c `{code}` ({e})")
                await TimingJitter.wait(1500, 4000)
        e = embed("\U0001f4d6 Replay Complete", "\n".join(results), GREEN)
        await interaction.edit_original_response(content=None, embed=e, view=BackToDash())

    @app_commands.command(name="bookworkreplay", description="Submit all cached bookwork codes")
    async def bookworkreplay_cmd(self, interaction: discord.Interaction):
        await self.replay_all_bookwork(interaction)

    # ══════════════════════════════════════════
    #  BOOKWORK CLEAR
    # ══════════════════════════════════════════

    @app_commands.command(name="bookworkclear", description="Clear cached bookwork codes")
    async def bookworkclear_cmd(self, interaction: discord.Interaction):
        clear_bookwork_temp(interaction.user.id)
        await interaction.response.send_message(embed=embed("\u2705 Cleared", "Bookwork cache emptied.", GREEN), ephemeral=True)

    # ══════════════════════════════════════════
    #  STATUS
    # ══════════════════════════════════════════

    @app_commands.command(name="status", description="Check stealth system")
    async def status_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = load_session(interaction.user.id)
        acct_count = len(data.get("accounts", [])) if data else 0
        bwc = count_bookwork_temp(interaction.user.id)
        e = embed("\U0001f6e1\ufe0f Stealth Status", "", BLURPLE)
        e.add_field(name="Accounts", value=f"`{acct_count}/3`", inline=True)
        e.add_field(name="Alt Bots", value=f"`{len(alt_clusters)}`", inline=True)
        e.add_field(name="Proxies", value=f"`{len(PROXY_LIST)}`", inline=True)
        e.add_field(name="Rate Limit", value="`0.5/s (burst 5)`", inline=True)
        e.add_field(name="Jitter", value="`300-1000ms`", inline=True)
        e.add_field(name="UA Rotation", value="`3 engines`", inline=True)
        e.add_field(name="Failover", value="`Auto`", inline=True)
        e.add_field(name="Bookwork Cached", value=f"`{bwc}`", inline=True)
        if acct_count < 3:
            e.add_field(name="\u26a0 Tip", value=f"Add {3-acct_count} more account{'s' if 3-acct_count>1 else ''} for full rotation.", inline=False)
        await interaction.followup.send(embed=e, view=BackToDash(), ephemeral=True)

    # ══════════════════════════════════════════
    #  LOGOUT
    # ══════════════════════════════════════════

    @app_commands.command(name="logout", description="Remove all accounts")
    async def logout_cmd(self, interaction: discord.Interaction):
        delete_session(interaction.user.id)
        await interaction.response.send_message(embed=embed("\u274c Logged out", "All accounts cleared.", RED), ephemeral=True)

    # ══════════════════════════════════════════
    #  PURGE
    # ══════════════════════════════════════════

    @app_commands.command(name="purge", description="[Owner] Delete all sessions")
    async def purge_cmd(self, interaction: discord.Interaction):
        if OWNER_ID and interaction.user.id != OWNER_ID:
            return await interaction.response.send_message(embed=embed("\u274c Owner only", "You need to be the owner.", RED), ephemeral=True)
        for uid in list_all_users():
            delete_session(uid)
        await interaction.response.send_message(embed=embed("\u2705 Purged", "All sessions deleted.", GREEN), ephemeral=True)

    # ══════════════════════════════════════════
    #  HELP
    # ══════════════════════════════════════════

    @app_commands.command(name="help", description="Show all commands")
    async def help_cmd(self, interaction: discord.Interaction):
        e = embed("\U0001f4ac Commands", "All available slash commands", BLURPLE)
        cmds = [
            ("\U0001f4a0 /dashboard", "Main command centre"),
            ("\U0001f511 /login <email> <password>", "Log in directly (no extension needed!)"),
            ("\U0001f511 /logincookies <cookies>", "Link via cookie string"),
            ("\u2795 /addaccount <email> <password>", "Add another account"),
            ("\U0001f465 /accounts", "View linked accounts"),
            ("\U0001f5d1 /removeaccount <email>", "Remove an account"),
            ("\U0001f464 /profile", "View Sparx profile"),
            ("\U0001f4da /homework", "View homework"),
            ("\U0001f4cb /tasks <id>", "View tasks"),
            ("\u2699 /autobookwork <id>", "Auto-submit bookwork"),
            ("\U0001f4d6 /bookwork", "Browse cached codes"),
            ("\u23ec /bookworkreplay", "Submit all cached codes"),
            ("\U0001f5d1 /bookworkclear", "Clear cache"),
            ("\U0001f6e1 /status", "Stealth settings"),
            ("\U0001f6aa /logout", "Remove all accounts"),
        ]
        for name, desc in cmds:
            e.add_field(name=name, value=desc, inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)


# ══════════════════════════════════════════════
#  VIEWS
# ══════════════════════════════════════════════

class QuickDash(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
    @discord.ui.button(label="\U0001f4a0 Dashboard", style=discord.ButtonStyle.primary)
    async def go(self, interaction: discord.Interaction, b: discord.ui.Button):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog.dashboard(interaction)

class BackToDash(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
    @discord.ui.button(label="\U0001f4a0 Back to Dashboard", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, b: discord.ui.Button):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog.dashboard(interaction)

class MainMenu(discord.ui.View):
    def __init__(self, logged_in: bool, has_bookwork: bool):
        super().__init__(timeout=180)
        if logged_in:
            self.add_item(MenuBtn("\U0001f4da Homework", "homework", discord.ButtonStyle.primary))
            self.add_item(MenuBtn("\U0001f4d6 Bookwork", "bookwork_cmd", discord.ButtonStyle.primary))
            self.add_item(MenuBtn("\U0001f464 Profile", "profile", discord.ButtonStyle.primary))
            self.add_item(MenuBtn("\U0001f465 Accounts", "accounts", discord.ButtonStyle.primary))
            self.add_item(MenuBtn("\U0001f6e1 Stealth", "status_cmd", discord.ButtonStyle.primary))
            self.add_item(MenuBtn("\U0001f6aa Logout", "logout_cmd", discord.ButtonStyle.danger))
        else:
            self.add_item(discord.ui.Button(
                label="\U0001f381 Get Cookie Extender (desktop)",
                url="https://github.com/DeterminedGeneral/Sparx-Cookie-Getter",
                style=discord.ButtonStyle.link
            ))

class MenuBtn(discord.ui.Button):
    def __init__(self, label: str, command: str, style: discord.ButtonStyle):
        super().__init__(style=style, label=label, custom_id=command)
    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("CommandCentre")
        if not cog:
            return
        cmd = interaction.client.tree.get_command(self.custom_id)
        if cmd:
            await cmd._callback(cog, interaction)

class HomeworkView(discord.ui.View):
    def __init__(self, hw_list: list, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id
        for hw in hw_list:
            hid = hw.get("id", "???")
            title = hw.get("title", "Untitled")[:40]
            self.add_item(HWBtn(f"\U0001f4cb {title}", hid))
    @discord.ui.button(label="\U0001f4a0 Dashboard", style=discord.ButtonStyle.secondary, row=2)
    async def dash(self, interaction: discord.Interaction, b: discord.ui.Button):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog.dashboard(interaction)

class HWBtn(discord.ui.Button):
    def __init__(self, label: str, homework_id: str):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, custom_id=f"hw:{homework_id}")
        self.hw_id = homework_id
    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog.show_tasks(interaction, self.hw_id)

class TasksView(discord.ui.View):
    def __init__(self, tasks_list: list, homework_id: str, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.homework_id = homework_id
        for t in tasks_list:
            tid = t.get("id", "???")
            title = t.get("title", "Untitled")[:35]
            self.add_item(TaskBtn(f"\u2699 {title}", tid))
        self.add_item(AutoBWBtn(homework_id))
    @discord.ui.button(label="\U0001f4a0 Dashboard", style=discord.ButtonStyle.secondary, row=2)
    async def dash(self, interaction: discord.Interaction, b: discord.ui.Button):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog.dashboard(interaction)

class TaskBtn(discord.ui.Button):
    def __init__(self, label: str, task_id: str):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, custom_id=f"task:{task_id}")
        self.task_id = task_id
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=embed("\U0001f4cb Task", f"Task ID: `{self.task_id}`\nUse `/autobookwork` with the homework ID to auto-submit bookwork.", BLURPLE),
            ephemeral=True
        )

class AutoBWBtn(discord.ui.Button):
    def __init__(self, homework_id: str):
        super().__init__(style=discord.ButtonStyle.success, label="\u2699 Auto Bookwork", custom_id=f"abw:{homework_id}")
        self.hw_id = homework_id
    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog.run_autobookwork(interaction, self.hw_id)

class BookworkBrowseView(discord.ui.View):
    def __init__(self, user_id: int, page: int, total_pages: int, page_items: list):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.page = page
        self.total_pages = total_pages

        for code, data in page_items:
            label = f"\u2705 {code}"[:80]
            self.add_item(BWSubmitBtn(code, label))

        if total_pages > 1:
            if page > 0:
                self.add_item(PageBtn("◀ Prev", page - 1, discord.ButtonStyle.secondary))
            if page < total_pages - 1:
                self.add_item(PageBtn("Next ▶", page + 1, discord.ButtonStyle.secondary))

        self.add_item(ReplayAllBtn())
        self.add_item(ClearCacheBtn())

    @discord.ui.button(label="\U0001f4a0 Dashboard", style=discord.ButtonStyle.secondary, row=4)
    async def dash(self, interaction: discord.Interaction, b: discord.ui.Button):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog.dashboard(interaction)

class BWSubmitBtn(discord.ui.Button):
    def __init__(self, code: str, label: str):
        super().__init__(style=discord.ButtonStyle.success, label=label, custom_id=f"bw-submit:{code}")
        self.code = code
    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog.submit_single_code(interaction, self.code)

class PageBtn(discord.ui.Button):
    def __init__(self, label: str, page: int, style: discord.ButtonStyle):
        super().__init__(style=style, label=label, custom_id=f"bw-page:{page}")
        self.page = page
    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog._show_bookwork_page(interaction, self.page)

class ReplayAllBtn(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.primary, label="\U0001f504 Replay All", custom_id="bw-replay-all")
    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("CommandCentre")
        if cog:
            await cog.replay_all_bookwork(interaction)

class ClearCacheBtn(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="\U0001f5d1 Clear Cache", custom_id="bw-clear")
    async def callback(self, interaction: discord.Interaction):
        clear_bookwork_temp(interaction.user.id)
        await interaction.response.send_message(embed=embed("\u2705 Cache cleared", "", GREEN), ephemeral=True)


# ══════════════════════════════════════════════
#  BOT
# ══════════════════════════════════════════════

class SparxBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix=None, intents=intents, help_command=None)

    async def setup_hook(self):
        proxy_rotator._proxies = PROXY_LIST
        proxy_rotator._index = random.randint(0, 999)
        await self.add_cog(CommandCentre(self))

        target = discord.Object(id=GUILD_ID) if GUILD_ID else None
        if target:
            self.tree.copy_global_to(guild=target)
            await self.tree.sync(guild=target)
        else:
            await self.tree.sync()

        for uid in list_all_users():
            data = load_session(uid)
            if data and data.get("accounts"):
                for acct in data["accounts"]:
                    await session_manager.add_session(uid, acct["cookie"], acct["info"])
                print(f"   Loaded {len(data['accounts'])} account(s) for user {uid}")

        if ALT_TOKENS:
            asyncio.create_task(start_alt_bots())

        print(f"\u2705 Synced. Logged in as {self.user}")

    async def on_ready(self):
        total = 0
        for uid in list_all_users():
            d = load_session(uid)
            if d:
                total += len(d.get("accounts", []))
        print(f"   Total accounts: {total} | Proxies: {len(PROXY_LIST)} | Alts: {len(alt_clusters)}")


bot = SparxBot()

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("\u274c Set BOT_TOKEN in .env")
    else:
        try:
            bot.run(BOT_TOKEN)
        except discord.LoginFailure:
            print("\u274c Invalid token")
        except Exception as e:
            print(f"\u274c {e}")
