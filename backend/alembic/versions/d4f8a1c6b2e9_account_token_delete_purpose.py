"""account_token purpose delete_account (회원 탈퇴 메일 확인)

Revision ID: d4f8a1c6b2e9
Revises: c7a2e5f81d30
Create Date: 2026-10-30 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd4f8a1c6b2e9'
down_revision: Union[str, Sequence[str], None] = 'c7a2e5f81d30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """비밀번호 없는 소셜 계정이 인증된 이메일로 탈퇴를 확인하는 30분 1회용 토큰(app.account_recovery.PURPOSE_DELETE)."""
    op.drop_constraint('ck_account_token_purpose', 'account_token', type_='check')
    op.create_check_constraint('ck_account_token_purpose', 'account_token',
                               "purpose IN ('verify_email', 'reset_password', 'oauth_login', 'delete_account')")


def downgrade() -> None:
    """탈퇴 확인 토큰은 지운다(대기 중인 탈퇴 확인 링크는 무효가 된다)."""
    op.execute("DELETE FROM account_token WHERE purpose = 'delete_account'")
    op.drop_constraint('ck_account_token_purpose', 'account_token', type_='check')
    op.create_check_constraint('ck_account_token_purpose', 'account_token',
                               "purpose IN ('verify_email', 'reset_password', 'oauth_login')")
