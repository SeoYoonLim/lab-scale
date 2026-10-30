"""보안 이벤트 감사 로그(로거 app.security). 한 줄: event=<이름> user=<id 또는 -> result=<ok|fail|locked|...> ip=<마스킹한 IP>.

남기는 것: 이벤트 이름, user id(숫자), 결과, IP 앞부분(IPv4는 /24, IPv6는 /48). 남기지 않는 것: 아이디·이메일·비밀번호·토큰·코드·질문 원문.
아이디를 남기지 않는 이유: 로그인 실패의 아이디는 비밀번호를 잘못 친 경우 비밀번호 자체일 수 있다.
"""

import ipaddress
import logging

logger = logging.getLogger("app.security")

EVENTS = (
    "signup", "login", "login_locked", "logout", "password_change", "password_reset", "email_change",
    "email_verify", "oauth_login", "account_delete", "account_delete_requested",
)


def mask_ip(ip: str | None) -> str:
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return "-"
    prefix = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def event(name: str, *, user_id: int | None = None, result: str = "ok", ip: str | None = None) -> None:
    if name not in EVENTS:
        raise ValueError("unknown security event")
    uid = str(int(user_id)) if user_id is not None else "-"
    logger.info("event=%s user=%s result=%s ip=%s", name, uid, result, mask_ip(ip))
