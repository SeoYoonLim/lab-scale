"""add users.token_version

Revision ID: e2a9c4f71b03
Revises: c5d8f3a07b14
Create Date: 2026-10-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2a9c4f71b03'
down_revision: Union[str, Sequence[str], None] = 'c5d8f3a07b14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    토큰 폐기용 버전(JWT tv 클레임과 비교). 기존 행은 server_default로 0이 되고, tv가 없는 옛 토큰도 0으로 보므로
    적용 직후에도 기존 로그인 상태가 유지된다.
    """
    op.add_column(
        'users',
        sa.Column('token_version', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'token_version')
