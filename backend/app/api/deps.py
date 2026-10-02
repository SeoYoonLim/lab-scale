"""라우터 간에 공유하는 FastAPI 의존성.

회원가입/로그인이 없는 서비스라, 프론트가 생성해 localStorage에 저장하는 디바이스ID(uuid)를
`X-Device-Id` 헤더로 받아 그대로 신뢰해서 사용자를 구분한다. 인증이 아니라 단순 구분자다
(헤더 값을 바꾸면 다른 사람의 관심종목/모의투자 상태를 볼 수 있다는 뜻이고, 설계상 받아들인 한계다).
"""

from fastapi import Header

MAX_DEVICE_ID_LEN = 100


def get_device_id(x_device_id: str = Header(alias="X-Device-Id", min_length=1, max_length=MAX_DEVICE_ID_LEN)) -> str:
    return x_device_id
