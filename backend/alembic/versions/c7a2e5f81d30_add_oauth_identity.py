"""add oauth_identity, nullable users.password_hash, account_token purpose oauth_login

Revision ID: c7a2e5f81d30
Revises: b6e1d4a9c2f7
Create Date: 2026-10-30 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a2e5f81d30'
down_revision: Union[str, Sequence[str], None] = 'b6e1d4a9c2f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 다운그레이드 때 비밀번호 없는 계정(NULL)에 넣는 값. argon2 해시 형식이 아니라서 어떤 비밀번호로도 검증되지 않는다
# (app.auth.verify_password가 InvalidHashError를 False로 처리). 로그인은 계속 불가, 비밀번호 찾기로 새로 정할 수 있다.
NO_LOGIN_MARKER = '!no-password'


def upgrade() -> None:
    """Upgrade schema.

    - users.password_hash NULL 허용: 소셜 로그인으로 만든 계정은 비밀번호가 없다(아이디/비밀번호 로그인은 항상 실패).
    - oauth_identity: (provider, provider_user_id) 유일, 사용자 삭제 시 함께 삭제.
    - account_token.purpose에 'oauth_login'(소셜 로그인 뒤 프론트에 넘기는 60초 1회용 코드) 추가.
    """
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=True)
    op.create_table(
        'oauth_identity',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('provider_user_id', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("provider IN ('google', 'naver')", name='ck_oauth_identity_provider'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'provider_user_id', name='uq_oauth_identity_provider_user'),
    )
    op.create_index('idx_oauth_identity_user', 'oauth_identity', ['user_id'], unique=False)
    op.drop_constraint('ck_account_token_purpose', 'account_token', type_='check')
    op.create_check_constraint('ck_account_token_purpose', 'account_token',
                               "purpose IN ('verify_email', 'reset_password', 'oauth_login')")


def downgrade() -> None:
    """Downgrade schema.

    비밀번호 없는 계정은 지우지 않고 검증되지 않는 표식 값을 넣어 NOT NULL로 되돌린다(데이터를 잃지 않는다).
    소셜 연결과 1회용 로그인 코드는 사라진다(그 사용자는 비밀번호 찾기로 비밀번호를 정해야 로그인할 수 있다).
    """
    op.execute("DELETE FROM account_token WHERE purpose = 'oauth_login'")
    op.drop_constraint('ck_account_token_purpose', 'account_token', type_='check')
    op.create_check_constraint('ck_account_token_purpose', 'account_token', "purpose IN ('verify_email', 'reset_password')")
    op.drop_index('idx_oauth_identity_user', table_name='oauth_identity')
    op.drop_table('oauth_identity')
    op.execute(sa.text("UPDATE users SET password_hash = :m WHERE password_hash IS NULL").bindparams(m=NO_LOGIN_MARKER))
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=False)
