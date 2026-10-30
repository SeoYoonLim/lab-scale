from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, String, TIMESTAMP, func

from app.db.base import Base


class AccountToken(Base):
    """이메일 인증·비밀번호 재설정용 1회용 토큰(app/account_recovery.py).

    원문 토큰은 저장하지 않는다. token_hash는 원문의 SHA-256 hex다(원문은 메일 링크에만 있다).
    used_at이 있으면 쓴 토큰이다. 같은 목적의 새 토큰을 발급하거나 이메일을 바꾸거나 비밀번호를 재설정하면
    남은 미사용 토큰에도 used_at을 채워 무효로 한다(별도 컬럼 없이 "더는 쓸 수 없음"을 한 곳에서 판정).
    """

    __tablename__ = "account_token"
    __table_args__ = (
        # oauth_login(2026-10-30): 소셜 로그인 콜백이 프론트에 넘기는 60초 1회용 코드(app/oauth_db.py)
        CheckConstraint("purpose IN ('verify_email', 'reset_password', 'oauth_login')", name="ck_account_token_purpose"),
        Index("uq_account_token_hash", "token_hash", unique=True),
        Index("idx_account_token_user_purpose", "user_id", "purpose"),
    )

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose = Column(String(20), nullable=False)
    token_hash = Column(String(64), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(TIMESTAMP(timezone=True), nullable=False)
    used_at = Column(TIMESTAMP(timezone=True))

    def __repr__(self) -> str:
        # 해시도 로그에 찍히지 않게 한다.
        return f"<AccountToken id={self.id} user_id={self.user_id} purpose={self.purpose!r}>"
