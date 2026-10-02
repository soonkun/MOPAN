# ruff: noqa: E501 - 아래 HTML 메일 템플릿 줄은 길다
"""메일 한 통 보내기 - 계정 생성·임시 비밀번호 안내용(app/users/router.py).

SMTP 설정은 .env(SMTP_HOST/PORT/USER/PASSWORD/FROM). 일일 리포트(mopan-report/smtp.env)와 같은 Gmail 계정.
설정이 비어 있으면 보내지 않고 False - 화면이 "직접 전달해 주세요"로 안내한다. 보내는 일은 스레드에서
(smtplib은 동기).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from html import escape as html_escape
from urllib.parse import quote

from app.core.config import Settings

logger = logging.getLogger("mopan.mail")


def configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def _send(settings: Settings, to: str, subject: str, body: str, html: str | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")  # 버튼이 있는 HTML, 못 그리는 클라이언트는 위 텍스트
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


async def send_mail(settings: Settings, to: str, subject: str, body: str, html: str | None = None) -> bool:
    """성공이면 True. 실패는 로그로 남기고 False - 계정 생성 자체는 이미 끝났으니 예외로 되돌리지 않는다."""
    if not configured(settings):
        return False
    try:
        await asyncio.to_thread(_send, settings, to, subject, body, html)
        return True
    except Exception:  # noqa: BLE001 - SMTP·네트워크 어떤 실패든 같은 처리
        logger.warning("mail_failed to=%s subject=%s", to, subject, exc_info=True)
        return False


def account_mail(kind: str, *, url: str, email: str, temporary: str) -> tuple[str, str, str]:
    """(제목, 텍스트 본문, HTML 본문). kind = created | reset. HTML에는 로그인 화면으로 가는 버튼 하나 -
    이메일이 미리 채워지고(login?email=…) 임시 비밀번호만 붙여 넣으면 된다(소유자 요청 2026-09-27)."""
    what = "MOPAN 계정이 만들어졌습니다" if kind == "created" else "MOPAN 임시 비밀번호가 발급되었습니다"
    login = f"{url}/login?email={quote(email)}"
    body = f"""{what}.

접속(로그인): {login}
아이디(이메일): {email}
임시 비밀번호: {temporary}

로그인한 뒤 왼쪽 아래 계정 메뉴 → 비밀번호 변경에서 바로 새 비밀번호로 바꿔 주세요.
이 메일을 요청하지 않았다면 관리자에게 알려 주세요.
"""
    e = html_escape
    html = f"""<!doctype html><html lang="ko"><body style="margin:0;padding:24px;background:#f4f6fa;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:#1b1c1e">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="480" cellspacing="0" cellpadding="0" style="max-width:480px;background:#ffffff;border-radius:16px;padding:32px">
<tr><td style="font-size:20px;font-weight:600;padding-bottom:12px">{e(what)}</td></tr>
<tr><td style="font-size:15px;line-height:1.6;color:#44474e;padding-bottom:20px">아래 버튼을 누르면 로그인 화면이 열리고 이메일이 미리 채워져 있습니다. 임시 비밀번호만 넣어 주세요.</td></tr>
<tr><td align="center" style="padding:4px 0 24px"><a href="{e(login)}" style="display:inline-block;background:#1f5eff;color:#ffffff;text-decoration:none;font-size:16px;font-weight:600;padding:14px 36px;border-radius:999px">MOPAN 접속하기</a></td></tr>
<tr><td style="background:#f1f4f9;border-radius:12px;padding:16px;font-size:15px;line-height:1.8">
아이디(이메일): <b>{e(email)}</b><br>임시 비밀번호: <b style="font-family:Menlo,Consolas,monospace;font-size:17px;letter-spacing:1px">{e(temporary)}</b></td></tr>
<tr><td style="font-size:13px;line-height:1.6;color:#6b6f78;padding-top:20px">로그인한 뒤 왼쪽 아래 계정 메뉴 → 비밀번호 변경에서 바로 새 비밀번호로 바꿔 주세요.<br>버튼이 열리지 않으면 이 주소를 붙여 넣으세요: <a href="{e(login)}" style="color:#1f5eff">{e(login)}</a><br>이 메일을 요청하지 않았다면 관리자에게 알려 주세요.</td></tr>
</table></td></tr></table></body></html>"""
    return f"[MOPAN] {what}", body, html
