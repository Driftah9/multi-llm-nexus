"""Mattermost REST + WebSocket API client."""

import logging
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)


class MattermostAPI:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def ws_url(self) -> str:
        return self.base_url.replace("http", "ws", 1).replace("/api/v4", "") + "/api/v4/websocket"

    async def start(self):
        self._session = aiohttp.ClientSession(headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })

    async def stop(self):
        if self._session:
            await self._session.close()

    async def get(self, path: str) -> dict:
        async with self._session.get(f"{self.base_url}{path}") as resp:
            return await resp.json()

    async def post(self, path: str, data: dict) -> dict:
        async with self._session.post(f"{self.base_url}{path}", json=data) as resp:
            return await resp.json()

    async def put(self, path: str, data: dict) -> dict:
        async with self._session.put(f"{self.base_url}{path}", json=data) as resp:
            return await resp.json()

    async def delete(self, path: str) -> None:
        async with self._session.delete(f"{self.base_url}{path}") as resp:
            if resp.status not in (200, 204):
                body = await resp.read()
                raise Exception(f"DELETE {path} returned {resp.status}: {body.decode()[:200]}")

    async def get_bot_user(self) -> dict:
        return await self.get("/users/me")

    async def get_team_by_name(self, name: str) -> dict:
        return await self.get(f"/teams/name/{name}")

    async def get_channel(self, channel_id: str) -> dict:
        return await self.get(f"/channels/{channel_id}")

    async def get_user(self, user_id: str) -> dict:
        return await self.get(f"/users/{user_id}")

    async def post_message(self, channel_id: str, message: str, root_id: str = "") -> dict:
        data = {"channel_id": channel_id, "message": message}
        if root_id:
            data["root_id"] = root_id
        return await self.post("/posts", data)

    async def update_message(self, post_id: str, message: str) -> dict:
        return await self.put(f"/posts/{post_id}/patch", {"message": message})

    async def delete_post(self, post_id: str) -> None:
        await self.delete(f"/posts/{post_id}")

    async def set_typing(self, channel_id: str) -> None:
        try:
            await self.post("/users/me/typing", {"channel_id": channel_id})
        except Exception:
            pass

    async def get_channel_posts(self, channel_id: str, per_page: int = 200, page: int = 0) -> dict:
        return await self.get(f"/channels/{channel_id}/posts?per_page={per_page}&page={page}")

    async def create_channel(self, team_id: str, name: str, display_name: str, purpose: str = "") -> dict:
        return await self.post("/channels", {
            "team_id": team_id, "name": name,
            "display_name": display_name, "purpose": purpose, "type": "O",
        })

    async def add_user_to_channel(self, channel_id: str, user_id: str) -> dict:
        return await self.post(f"/channels/{channel_id}/members", {"user_id": user_id})

    async def get_user_channels(self, user_id: str, team_id: str) -> list[dict]:
        result = await self.get(f"/users/{user_id}/teams/{team_id}/channels")
        return result if isinstance(result, list) else []

    async def download_file(self, file_id: str) -> bytes:
        async with self._session.get(f"{self.base_url}/files/{file_id}") as resp:
            return await resp.read()

    async def get_pinned_posts(self, channel_id: str) -> set[str]:
        try:
            data = await self.get(f"/channels/{channel_id}/pinned")
            return set(data.get("posts", {}).keys())
        except Exception:
            return set()
