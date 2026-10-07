from sqlalchemy import BigInteger, CheckConstraint, Column, String, TIMESTAMP, UniqueConstraint, func

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
    )

    id = Column(BigInteger, primary_key=True)
    username = Column(String(20), nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    def __repr__(self) -> str:
        # 기본 repr이 로그/예외 메시지로 새어 나가도 해시가 찍히지 않게 한다.
        return f"<User id={self.id} username={self.username!r}>"
