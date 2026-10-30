from sqlalchemy import BigInteger, CheckConstraint, Column, Index, Integer, String, TIMESTAMP, UniqueConstraint, func, text

from app.db.base import Base


class User(Base):
    """아이디/비밀번호로 가입한 사용자.

    테이블 이름이 `user`가 아닌 이유: PostgreSQL에서 user는 예약어라 따옴표 없이 `SELECT * FROM user`를
    쓰면 테이블이 아니라 현재 DB 접속 계정(current_user)이 조회된다.

    username은 가입 시 소문자로 정규화해 저장한다(CHECK로 DB에서도 보장). password_hash는 argon2id 해시이고
    평문 비밀번호는 어디에도 저장하지 않는다. password_hash는 어떤 API 응답에도 내보내지 않는다.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint("username = lower(username)", name="ck_users_username_lowercase"),
        # 이메일은 소문자·공백 제거로 정규화해 저장하고(app.account_recovery.normalize_email) 대소문자 무시로 유일하다.
        CheckConstraint("email = lower(btrim(email))", name="ck_users_email_normalized"),
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    id = Column(BigInteger, primary_key=True)
    username = Column(String(20), nullable=False)
    # 소셜 로그인(2026-10-30)으로 만든 계정은 NULL이다. NULL이면 아이디/비밀번호 로그인은 항상 실패하고(app.auth.authenticate),
    # 비밀번호 변경·이메일 변경은 400이며, 인증된 이메일이 있으면 비밀번호 찾기로 새로 정할 수 있다.
    password_hash = Column(String(255), nullable=True)
    # 로그아웃·비밀번호 변경 때 1씩 올린다. JWT의 tv 클레임과 다르면 그 토큰은 폐기된 것이다(app.auth).
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    # 계정 복구(2026-10-30). 선택 입력이고, 복구 메일은 email_verified_at이 있는(인증된) 이메일에만 보낸다.
    email = Column(String(254))
    email_verified_at = Column(TIMESTAMP(timezone=True))

    def __repr__(self) -> str:
        # 기본 repr이 로그/예외 메시지로 새어 나가도 해시가 찍히지 않게 한다.
        return f"<User id={self.id} username={self.username!r}>"
