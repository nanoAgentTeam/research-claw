from __future__ import annotations

import importlib
import json
import os
import smtplib
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse
from urllib.request import Request, urlopen
from email.message import EmailMessage


def build_apprise_url(
    channel: str,
    *,
    chat_id: str = "",
    params: Optional[dict[str, Any]] = None,
    apprise_url: str = "",
) -> str:
    ch = str(channel or "").strip().lower()
    if ch.startswith("im_"):
        ch = ch[3:]
    payload = params or {}

    explicit_url = str(apprise_url or payload.get("apprise_url") or "").strip()
    if explicit_url:
        return explicit_url

    if ch in {"wechat", "wecom", "wecombot"}:
        key = str(payload.get("key") or payload.get("botkey") or payload.get("token") or chat_id).strip()
        return f"wecombot://{key}" if key else ""

    if ch in {"serverchan", "server_chan", "sct"}:
        sendkey = str(payload.get("sendkey") or payload.get("send_key") or payload.get("token") or chat_id).strip()
        return f"schan://{sendkey}" if sendkey else ""

    if ch == "feishu":
        webhook_url = str(payload.get("webhook_url") or "").strip()
        if not webhook_url:
            token = str(payload.get("token") or "").strip()
            if token:
                webhook_url = f"https://open.feishu.cn/open-apis/bot/v2/hook/{token}"
        if not webhook_url:
            cid = str(chat_id).strip()
            if cid:
                return f"im-direct://feishu/{cid}"
            return ""
        # 去掉 https:// 前缀，用 feishu-webhook:// 标记走自定义 HTTP POST
        return "feishu-webhook://" + webhook_url.replace("https://", "").replace("http://", "")

    if ch in {"dingtalk", "dingding", "钉钉"}:
        webhook_url = str(payload.get("webhook_url") or "").strip()
        secret = str(payload.get("secret") or "").strip()

        if not webhook_url:
            token = str(payload.get("token") or payload.get("access_token") or "").strip()
            if token:
                webhook_url = f"https://oapi.dingtalk.com/robot/send?access_token={token}"
        if not webhook_url:
            cid = str(chat_id).strip()
            if cid:
                return f"im-direct://dingtalk/{cid}"
            return ""

        # 去掉 https:// 前缀，用 dingtalk-webhook:// 标记走自定义 HTTP POST
        # 如果有 secret，附加到 URL 中用于签名
        base_url = "dingtalk-webhook://" + webhook_url.replace("https://", "").replace("http://", "")
        if secret:
            # 使用特殊分隔符传递 secret（不会出现在实际 URL 中）
            base_url += f"#secret={secret}"
        return base_url

    if ch == "qq":
        token = str(payload.get("token") or "").strip()
        if not token:
            cid = str(chat_id).strip()
            if cid:
                return f"im-direct://qq/{cid}"
            return ""
        mode_raw = str(payload.get("mode") or "send").strip().lower()
        mode_map = {
            "private": "send",
            "send": "send",
            "group": "group",
            "json_private": "jsend",
            "jsend": "jsend",
            "json_group": "jgroup",
            "jgroup": "jgroup",
        }
        endpoint = mode_map.get(mode_raw, "send")
        qmsg_params: dict[str, str] = {}
        qq = str(payload.get("qq") or "").strip()
        bot = str(payload.get("bot") or "").strip()
        if qq:
            qmsg_params["qq"] = qq
        if bot:
            qmsg_params["bot"] = bot
        qs = urlencode(qmsg_params)
        return f"qmsg://{endpoint}/{token}" + (f"?{qs}" if qs else "")

    if ch == "telegram":
        bot_token = str(payload.get("bot_token") or payload.get("token") or "").strip()
        t_chat_id = str(payload.get("chat_id") or chat_id).strip()
        if not bot_token:
            if t_chat_id:
                return f"im-direct://telegram/{t_chat_id}"
            return ""
        if t_chat_id:
            return f"tgram://{bot_token}/{t_chat_id}"
        return f"tgram://{bot_token}/"

    if ch == "email":
        recipient = str(payload.get("email") or payload.get("to") or chat_id).strip()
        if not recipient:
            return ""
        profile_id = str(payload.get("profile_id") or payload.get("smtp_profile_id") or payload.get("profile") or "").strip()
        qs = urlencode({"profile_id": profile_id}) if profile_id else ""
        return f"email://{recipient}" + (f"?{qs}" if qs else "")

    return ""


def send_apprise_notification(url: str, title: str, body: str) -> tuple[bool, str]:
    target = str(url or "").strip()
    if not target:
        return False, "empty apprise url"

    if target.startswith("im-direct://"):
        return False, "IM-direct subscriptions are delivered through the IM runtime bus"

    if target.startswith("email://"):
        parsed = urlparse(target)
        recipient = str(parsed.netloc or parsed.path.lstrip("/")).strip()
        if not recipient:
            return False, "empty email recipient"
        query = parse_qs(parsed.query or "")
        profile_id = str((query.get("profile_id") or [""])[0]).strip()
        try:
            profile = _resolve_smtp_profile(profile_id)
            _send_email_sync(recipient, title, body, smtp_profile=profile)
            return True, "ok"
        except Exception as e:
            return False, f"email send failed: {e}"

    if target.startswith("qmsg://"):
        try:
            parsed = urlparse(target)
            endpoint = str(parsed.netloc or "").strip()
            token = str(parsed.path or "").strip().lstrip("/")
            if not endpoint or not token:
                return False, f"invalid qmsg target: {target}"
            base_url = f"https://qmsg.zendee.cn/{endpoint}/{token}"
            query = parse_qs(parsed.query)

            msg = str((title or "").strip())
            body_text = str(body or "").strip()
            if body_text:
                msg = f"{msg}\n\n{body_text}" if msg else body_text
            form_data: dict[str, str] = {"msg": msg or "Open Research Claw Notification"}
            qq = str((query.get("qq") or [""])[0]).strip()
            bot = str((query.get("bot") or [""])[0]).strip()
            if qq:
                form_data["qq"] = qq
            if bot:
                form_data["bot"] = bot

            if endpoint in {"jsend", "jgroup"}:
                data = json.dumps(form_data, ensure_ascii=False).encode("utf-8")
                req = Request(base_url, data=data, method="POST")
                req.add_header("Content-Type", "application/json")
            else:
                data = urlencode(form_data).encode("utf-8")
                req = Request(base_url, data=data, method="POST")
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            payload = json.loads(raw) if raw else {}
            if bool(payload.get("success")):
                return True, "ok"
            if isinstance(payload, dict) and payload:
                # Keep full upstream JSON so UI can show precise Qmsg failure context.
                return False, json.dumps(payload, ensure_ascii=False)
            reason = str(payload.get("reason") or "qmsg notify failed").strip()
            return False, reason
        except HTTPError as e:
            raw = ""
            try:
                body = e.read()
                if body:
                    raw = body.decode("utf-8", errors="ignore")
            except Exception:
                raw = ""
            if raw:
                try:
                    payload = json.loads(raw)
                    if isinstance(payload, dict) and payload:
                        return False, json.dumps(payload, ensure_ascii=False)
                except Exception:
                    pass
                return False, f"qmsg notify failed: HTTP {e.code}: {raw}"
            reason = str(getattr(e, "reason", "") or "").strip()
            if reason:
                return False, f"qmsg notify failed: HTTP Error {e.code}: {reason}"
            return False, f"qmsg notify failed: HTTP Error {e.code}"
        except Exception as e:
            return False, f"qmsg notify failed: {e}"

    if target.startswith("feishu-webhook://"):
        try:
            actual_url = "https://" + target[len("feishu-webhook://"):]
            msg = str((title or "").strip())
            body_text = str(body or "").strip()
            if body_text:
                msg = f"{msg}\n\n{body_text}" if msg else body_text
            payload = json.dumps(
                {"msg_type": "text", "content": {"text": msg or "Open Research Claw Notification"}},
                ensure_ascii=False,
            ).encode("utf-8")
            req = Request(actual_url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            result = json.loads(raw) if raw else {}
            if result.get("code") == 0 or result.get("StatusCode") == 0:
                return True, "ok"
            reason = str(result.get("msg") or result.get("StatusMessage") or "feishu notify failed").strip()
            return False, reason
        except Exception as e:
            return False, f"feishu notify failed: {e}"

    if target.startswith("dingtalk-webhook://"):
        try:
            import time
            import hmac
            import hashlib
            import base64
            # 解析 URL 和 secret
            target_without_prefix = target[len("dingtalk-webhook://"):]
            secret = None
            if "#secret=" in target_without_prefix:
                target_without_prefix, secret_part = target_without_prefix.split("#secret=", 1)
                secret = secret_part

            actual_url = "https://" + target_without_prefix

            # 如果有 secret，添加签名参数
            if secret:
                timestamp = str(round(time.time() * 1000))
                secret_enc = secret.encode('utf-8')
                string_to_sign = f'{timestamp}\n{secret}'
                string_to_sign_enc = string_to_sign.encode('utf-8')
                hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
                sign = quote_plus(base64.b64encode(hmac_code))

                # 添加 timestamp 和 sign 参数
                separator = "&" if "?" in actual_url else "?"
                actual_url = f"{actual_url}{separator}timestamp={timestamp}&sign={sign}"

            msg = str((title or "").strip())
            body_text = str(body or "").strip()
            if body_text:
                msg = f"{msg}\n\n{body_text}" if msg else body_text
            payload = json.dumps(
                {"msgtype": "text", "text": {"content": msg or "Open Research Claw Notification"}},
                ensure_ascii=False,
            ).encode("utf-8")
            req = Request(actual_url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            result = json.loads(raw) if raw else {}
            if result.get("errcode") == 0:
                return True, "ok"
            reason = str(result.get("errmsg") or "dingtalk notify failed").strip()
            return False, reason
        except Exception as e:
            return False, f"dingtalk notify failed: {e}"

    try:
        apprise = importlib.import_module("apprise")
    except Exception as e:
        return False, f"apprise import failed: {e}"

    app = apprise.Apprise()
    if not app.add(target):
        return False, f"invalid apprise url: {target}"

    ok = app.notify(title=title or "Open Research Claw Notification", body=body or "")
    return (True, "ok") if ok else (False, "apprise notify failed")


def _resolve_smtp_profile(profile_id: str = "") -> Optional[dict[str, Any]]:
    try:
        from config.loader import get_config_service
        service = get_config_service()
        smtp_cfg = getattr(service.config, "smtp", None)
        profiles = getattr(smtp_cfg, "profiles", None) if smtp_cfg is not None else None
        if not isinstance(profiles, list):
            return None
        normalized: list[dict[str, Any]] = []
        for item in profiles:
            if hasattr(item, "model_dump"):
                row = item.model_dump()
            elif isinstance(item, dict):
                row = dict(item)
            else:
                continue
            row["id"] = str(row.get("id") or "").strip()
            row["enabled"] = bool(row.get("enabled", True))
            row["is_default"] = bool(row.get("is_default", False))
            normalized.append(row)
        if profile_id:
            return next((x for x in normalized if x.get("id") == profile_id and x.get("enabled")), None)
        default_profile = next((x for x in normalized if x.get("enabled") and x.get("is_default")), None)
        if default_profile:
            return default_profile
        return next((x for x in normalized if x.get("enabled")), None)
    except Exception:
        return None


def _send_email_sync(recipient: str, title: str, body: str, smtp_profile: Optional[dict[str, Any]] = None) -> None:
    to_addr = str(recipient or "").strip()
    if not to_addr:
        raise ValueError("empty email recipient")

    row = smtp_profile or {}
    host = str(row.get("host") or "").strip() or os.getenv("CONTEXT_BOT_SMTP_HOST", "").strip()
    port = int(row.get("port") or os.getenv("CONTEXT_BOT_SMTP_PORT", "587").strip() or "587")
    user = str(row.get("user") or "").strip() or os.getenv("CONTEXT_BOT_SMTP_USER", "").strip()
    password = str(row.get("password") or "")
    if not password:
        password = os.getenv("CONTEXT_BOT_SMTP_PASS", "").strip()
    from_email = str(row.get("from_email") or "").strip()
    from_name = str(row.get("from_name") or "").strip()
    sender = from_email or os.getenv("CONTEXT_BOT_SMTP_FROM", user).strip()
    use_tls = bool(row.get("use_tls", True))
    if smtp_profile is None:
        use_tls = os.getenv("CONTEXT_BOT_SMTP_TLS", "1").strip() not in {"0", "false", "False"}

    if not host or not sender:
        raise RuntimeError("smtp config missing: CONTEXT_BOT_SMTP_HOST / CONTEXT_BOT_SMTP_FROM")

    msg = EmailMessage()
    msg["Subject"] = title or "Open Research Claw Notification"
    msg["From"] = f"{from_name} <{sender}>" if from_name and sender else sender
    msg["To"] = to_addr
    msg.set_content(body or title or "Open Research Claw Notification")

    smtp_cls = smtplib.SMTP_SSL if (use_tls and port == 465) else smtplib.SMTP
    with smtp_cls(host, port, timeout=15) as smtp:
        if use_tls and port != 465:
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)
