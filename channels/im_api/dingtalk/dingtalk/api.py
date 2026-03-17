"""DingTalk HTTP API helper."""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import httpx

from .auth import auth_client
from .message_utils import detect_markdown_and_extract_title


class DingTalkAPI:
    def __init__(self, client_id: str, client_secret: str, robot_code: str = ""):
        self.client_id = client_id
        self.client_secret = client_secret
        self.robot_code = robot_code or client_id
        self.base_url = "https://api.dingtalk.com"

    async def get_access_token(self) -> str:
        return await auth_client.get_access_token(self.client_id, self.client_secret)

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = await self.get_access_token()
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json_data,
            )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}

    async def send_by_session(
        self,
        session_webhook: str,
        text: str,
        *,
        title: str | None = None,
        use_markdown: bool | None = None,
        at_user_id: str | None = None,
    ) -> dict[str, Any]:
        token = await self.get_access_token()
        options = {
            "title": title,
            "use_markdown": use_markdown,
        }
        markdown, resolved_title = detect_markdown_and_extract_title(text, options, "Open Research Claw")

        if markdown:
            body: dict[str, Any] = {
                "msgtype": "markdown",
                "markdown": {
                    "title": resolved_title,
                    "text": text,
                },
            }
        else:
            body = {
                "msgtype": "text",
                "text": {
                    "content": text,
                },
            }

        if at_user_id:
            body["at"] = {"atUserIds": [at_user_id], "isAtAll": False}

        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(session_webhook, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}

    async def send_proactive_text(
        self,
        target: str,
        text: str,
        *,
        title: str | None = None,
        use_markdown: bool | None = None,
    ) -> dict[str, Any]:
        target = str(target or "").strip()
        if not target:
            raise ValueError("target is required")

        is_group = target.startswith("cid")
        path = "/v1.0/robot/groupMessages/send" if is_group else "/v1.0/robot/oToMessages/batchSend"

        markdown, resolved_title = detect_markdown_and_extract_title(
            text,
            {"title": title, "use_markdown": use_markdown},
            "Open Research Claw",
        )

        payload: dict[str, Any] = {
            "robotCode": self.robot_code,
            "msgKey": "sampleMarkdown" if markdown else "sampleText",
            "msgParam": json.dumps({"title": resolved_title, "text": text} if markdown else {"content": text}),
        }

        if is_group:
            payload["openConversationId"] = target
        else:
            payload["userIds"] = [target]

        return await self._request("POST", path, payload)

    async def upload_media(self, file_path: str, media_type: str = "image") -> str:
        token = await self.get_access_token()
        path = pathlib.Path(file_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"media not found: {file_path}")

        upload_url = f"https://oapi.dingtalk.com/media/upload?access_token={token}&type={media_type}"
        mime = "application/octet-stream"
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".png":
            mime = "image/png"
        elif suffix == ".gif":
            mime = "image/gif"

        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(path, "rb") as f:
                files = {"media": (os.path.basename(path), f, mime)}
                resp = await client.post(upload_url, files=files)
        resp.raise_for_status()
        payload = resp.json()
        media_id = payload.get("media_id")
        if not media_id:
            raise RuntimeError(f"upload media failed: {payload}")
        return str(media_id)

    async def send_proactive_media(self, target: str, file_path: str, media_type: str = "image") -> dict[str, Any]:
        media_id = await self.upload_media(file_path, media_type)

        if media_type == "image":
            msg_key = "sampleImageMsg"
            msg_param = {"photoURL": media_id}
        elif media_type == "voice":
            msg_key = "sampleAudio"
            msg_param = {"mediaId": media_id, "duration": "0"}
        else:
            name = pathlib.Path(file_path).name
            ext = pathlib.Path(file_path).suffix.lstrip(".") or "file"
            msg_key = "sampleFile"
            msg_param = {"mediaId": media_id, "fileName": name, "fileType": ext}

        target = str(target or "").strip()
        is_group = target.startswith("cid")
        path = "/v1.0/robot/groupMessages/send" if is_group else "/v1.0/robot/oToMessages/batchSend"

        payload: dict[str, Any] = {
            "robotCode": self.robot_code,
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param),
        }
        if is_group:
            payload["openConversationId"] = target
        else:
            payload["userIds"] = [target]

        return await self._request("POST", path, payload)
