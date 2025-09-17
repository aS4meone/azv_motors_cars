"""add door/lock/window/light fields

Revision ID: 78b2b9f8d7a1
Revises: f28e98ff9ce4_
Create Date: 2025-09-17 23:59:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '78b2b9f8d7a1'
down_revision = 'f28e98ff9ce4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('vehicles') as batch_op:
        # Doors
        batch_op.add_column(sa.Column('front_right_door_open', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('front_left_door_open', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('rear_left_door_open', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('rear_right_door_open', sa.Boolean(), nullable=False, server_default=sa.false()))

        # Door locks
        batch_op.add_column(sa.Column('front_right_door_locked', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('front_left_door_locked', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('rear_left_door_locked', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('rear_right_door_locked', sa.Boolean(), nullable=False, server_default=sa.false()))

        # Central locks
        batch_op.add_column(sa.Column('central_locks_locked', sa.Boolean(), nullable=False, server_default=sa.false()))

        # Windows
        batch_op.add_column(sa.Column('front_left_window_closed', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('front_right_window_closed', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('rear_left_window_closed', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('rear_right_window_closed', sa.Boolean(), nullable=False, server_default=sa.false()))

        # Trunk / handbrake / lights
        batch_op.add_column(sa.Column('is_trunk_open', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('is_handbrake_on', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('are_lights_on', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('is_light_auto_mode_on', sa.Boolean(), nullable=False, server_default=sa.false()))

    # Clean server_default now that data is populated
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.alter_column('front_right_door_open', server_default=None)
        batch_op.alter_column('front_left_door_open', server_default=None)
        batch_op.alter_column('rear_left_door_open', server_default=None)
        batch_op.alter_column('rear_right_door_open', server_default=None)
        batch_op.alter_column('front_right_door_locked', server_default=None)
        batch_op.alter_column('front_left_door_locked', server_default=None)
        batch_op.alter_column('rear_left_door_locked', server_default=None)
        batch_op.alter_column('rear_right_door_locked', server_default=None)
        batch_op.alter_column('central_locks_locked', server_default=None)
        batch_op.alter_column('front_left_window_closed', server_default=None)
        batch_op.alter_column('front_right_window_closed', server_default=None)
        batch_op.alter_column('rear_left_window_closed', server_default=None)
        batch_op.alter_column('rear_right_window_closed', server_default=None)
        batch_op.alter_column('is_trunk_open', server_default=None)
        batch_op.alter_column('is_handbrake_on', server_default=None)
        batch_op.alter_column('are_lights_on', server_default=None)
        batch_op.alter_column('is_light_auto_mode_on', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.drop_column('is_light_auto_mode_on')
        batch_op.drop_column('are_lights_on')
        batch_op.drop_column('is_handbrake_on')
        batch_op.drop_column('is_trunk_open')
        batch_op.drop_column('rear_right_window_closed')
        batch_op.drop_column('rear_left_window_closed')
        batch_op.drop_column('front_right_window_closed')
        batch_op.drop_column('front_left_window_closed')
        batch_op.drop_column('central_locks_locked')
        batch_op.drop_column('rear_right_door_locked')
        batch_op.drop_column('rear_left_door_locked')
        batch_op.drop_column('front_left_door_locked')
        batch_op.drop_column('front_right_door_locked')
        batch_op.drop_column('rear_right_door_open')
        batch_op.drop_column('rear_left_door_open')
        batch_op.drop_column('front_left_door_open')
        batch_op.drop_column('front_right_door_open')


