"""add users.email/email_verified_at and account_token (account recovery)

Revision ID: b6e1d4a9c2f7
Revises: f4b81d29c6e5
Create Date: 2026-10-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6e1d4a9c2f7'
down_revision: Union[str, Sequence[str], None] = 'f4b81d29c6e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    users.email은 선택(NULL 가능)이라 기존 행은 그대로다(기존 계정은 로그인 후 이메일을 등록한다).
    저장값은 소문자·앞뒤 공백 제거로 정규화되고(CHECK), lower(email) 유일 인덱스로 대소문자 무시 유일이다(NULL은 여러 개 가능).
    account_token은 원문이 아니라 SHA-256 hex만 저장하고 token_hash가 유일하다. 사용자 삭제 시 함께 지워진다.
    """
    op.add_column('users', sa.Column('email', sa.String(length=254), nullable=True))
    op.add_column('users', sa.Column('email_verified_at', sa.TIMESTAMP(timezone=True), nullable=True))
    op.create_check_constraint('ck_users_email_normalized', 'users', 'email = lower(btrim(email))')
    op.create_index('uq_users_email_lower', 'users', [sa.text('lower(email)')], unique=True)

    op.create_table(
        'account_token',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('purpose', sa.String(length=20), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('used_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("purpose IN ('verify_email', 'reset_password')", name='ck_account_token_purpose'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_account_token_hash', 'account_token', ['token_hash'], unique=True)
    op.create_index('idx_account_token_user_purpose', 'account_token', ['user_id', 'purpose'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_account_token_user_purpose', table_name='account_token')
    op.drop_index('uq_account_token_hash', table_name='account_token')
    op.drop_table('account_token')
    op.drop_index('uq_users_email_lower', table_name='users')
    op.drop_constraint('ck_users_email_normalized', 'users', type_='check')
    op.drop_column('users', 'email_verified_at')
    op.drop_column('users', 'email')
