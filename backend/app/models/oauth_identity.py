from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Index, String, TIMESTAMP, UniqueConstraint, func

from app.db.base import Base


class OAuthIdentity(Base):
    """소셜 로그인(구글·네이버) 계정과 우리 사용자의 연결(app/oauth.py, app/oauth_db.py).

    (provider, provider_user_id)가 유일하다: 같은 공급자 계정은 한 사용자에게만 연결된다. provider_user_id는 공급자가 주는
    변하지 않는 식별자(구글 sub, 네이버 id)이고 이메일로 사용자를 찾지 않는다(이메일은 바뀔 수 있다). email은 연결 당시 공급자가 준 값(참고용).
    """

    __tablename__ = "oauth_identity"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_identity_provider_user"),
        CheckConstraint("provider IN ('google', 'naver')", name="ck_oauth_identity_provider"),
        Index("idx_oauth_identity_user", "user_id"),
    )

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(20), nullable=False)
    provider_user_id = Column(String(255), nullable=False)
    email = Column(String(254))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    def __repr__(self) -> str:
        return f"<OAuthIdentity id={self.id} user_id={self.user_id} provider={self.provider!r}>"
