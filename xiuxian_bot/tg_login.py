from __future__ import annotations

from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from .config import Config


@dataclass(frozen=True)
class TGLoginResult:
    authorized: bool = False
    password_required: bool = False


class TGLoginService:
    async def send_code(self, *, config: Config, session_name: str, phone: str) -> str:
        client = TelegramClient(session_name, config.tg_api_id, config.tg_api_hash)
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
            return str(sent.phone_code_hash)
        finally:
            await client.disconnect()

    async def sign_in_code(
        self,
        *,
        config: Config,
        session_name: str,
        phone: str,
        code: str,
        phone_code_hash: str,
    ) -> TGLoginResult:
        client = TelegramClient(session_name, config.tg_api_id, config.tg_api_hash)
        await client.connect()
        try:
            try:
                await client.sign_in(
                    phone=phone,
                    code=code,
                    phone_code_hash=phone_code_hash,
                )
            except SessionPasswordNeededError:
                return TGLoginResult(password_required=True)
            return TGLoginResult(authorized=True)
        finally:
            await client.disconnect()

    async def sign_in_password(
        self,
        *,
        config: Config,
        session_name: str,
        password: str,
    ) -> TGLoginResult:
        client = TelegramClient(session_name, config.tg_api_id, config.tg_api_hash)
        await client.connect()
        try:
            await client.sign_in(password=password)
            return TGLoginResult(authorized=True)
        finally:
            await client.disconnect()
