"""Thin async client for the Telegram Bot API (ADR-010). Only the calls Kharcha uses."""

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class TelegramError(Exception):
    def __init__(self, method: str, code: int, description: str) -> None:
        super().__init__(f"{method}: {code} {description}")
        self.code = code


@dataclass(frozen=True, slots=True)
class Button:
    text: str
    callback_data: str


class Messenger(Protocol):
    """What the bot needs from Telegram; faked in tests."""

    async def send_message(
        self, chat_id: int, text: str, buttons: list[list[Button]] | None = None
    ) -> int: ...

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None: ...

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None: ...


class TelegramClient:
    def __init__(self, token: str, http: httpx.AsyncClient, base_url: str) -> None:
        self._url = f"{base_url}/bot{token}"
        self._http = http

    async def _call(self, method: str, http_wait_s: float = 15.0, **params: Any) -> Any:
        response = await self._http.post(f"{self._url}/{method}", json=params, timeout=http_wait_s)
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(
                method, int(data.get("error_code", 0)), str(data.get("description"))
            )
        return data["result"]

    async def get_updates(self, offset: int | None, poll_s: int = 30) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": poll_s,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            params["offset"] = offset
        result = await self._call("getUpdates", http_wait_s=poll_s + 10, **params)
        return list(result)

    async def send_message(
        self, chat_id: int, text: str, buttons: list[list[Button]] | None = None
    ) -> int:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            params["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": b.text, "callback_data": b.callback_data} for b in row]
                    for row in buttons
                ]
            }
        result = await self._call("sendMessage", **params)
        return int(result["message_id"])

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        await self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        params: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
        await self._call("answerCallbackQuery", **params)
