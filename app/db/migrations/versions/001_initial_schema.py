"""Initial Schema Migration for Ledgerline

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-10-12 15:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. documents
    op.create_table(
        'documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('file_hash', sa.String(length=64), nullable=False),
        sa.Column('r2_storage_key', sa.String(length=512), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=False),
        sa.Column('file_size_bytes', sa.Integer(), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_documents_file_hash', 'documents', ['file_hash'])

    # 2. pages
    op.create_table(
        'pages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=False),
        sa.Column('page_hash', sa.String(length=64), nullable=False),
        sa.Column('is_native_text', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('raw_text', sa.Text(), nullable=True),
        sa.Column('layout_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('document_id', 'page_number', name='uq_document_page_number')
    )
    op.create_index('ix_pages_page_hash', 'pages', ['page_hash'])

    # 3. extraction_versions (Append-only time travel)
    op.create_table(
        'extraction_versions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('extractor_version', sa.String(length=50), nullable=False),
        sa.Column('prompt_version', sa.String(length=50), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_current', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index('ix_extraction_versions_time_travel', 'extraction_versions', ['document_id', 'valid_from', 'valid_to'])
    op.create_index('ix_extraction_versions_idempotency', 'extraction_versions', ['idempotency_key'])

    # 4. fields
    op.create_table(
        'fields',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('extraction_version_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('extraction_versions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('page_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('pages.id', ondelete='SET NULL'), nullable=True),
        sa.Column('field_name', sa.String(length=100), nullable=False),
        sa.Column('raw_value', sa.Text(), nullable=True),
        sa.Column('normalized_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('confidence_raw', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('confidence_calibrated', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('bounding_box', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_fields_field_name', 'fields', ['field_name'])

    # 5. field_features
    op.create_table(
        'field_features',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('field_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('fields.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('ocr_char_confidence', sa.Float(), nullable=True),
        sa.Column('token_probability', sa.Float(), nullable=True),
        sa.Column('layout_feature_vector', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('math_validation_passed', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('two_pass_agreement', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

    # 6. review_items (Row-level leases & optimistic concurrency)
    op.create_table(
        'review_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('field_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('fields.id', ondelete='SET NULL'), nullable=True, unique=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='pending'),
        sa.Column('leased_by', sa.String(length=100), nullable=True),
        sa.Column('leased_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('proposed_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('reviewed_value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('reviewed_by', sa.String(length=100), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_review_items_lease_state', 'review_items', ['status', 'lease_expires_at'])

    # 7. run_ledger
    op.create_table(
        'run_ledger',
        sa.Column('run_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='running'),
        sa.Column('watermark', sa.String(length=255), nullable=True),
        sa.Column('documents_processed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('documents_parked', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('canary_f1_score', sa.Float(), nullable=True),
        sa.Column('baseline_f1_score', sa.Float(), nullable=True),
        sa.Column('error_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # 8. dead_letter
    op.create_table(
        'dead_letter',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('file_hash', sa.String(length=64), nullable=True),
        sa.Column('failure_reason', sa.String(length=100), nullable=False),
        sa.Column('error_details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('parked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
    )
    op.create_index('ix_dead_letter_file_hash', 'dead_letter', ['file_hash'])

    # 9. cost_meter
    op.create_table(
        'cost_meter',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('page_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('routing_path', sa.String(length=50), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('estimated_cost_usd', sa.Numeric(precision=10, scale=6), nullable=False, server_default='0.000000'),
        sa.Column('latency_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    )

    # 10. backfill_progress
    op.create_table(
        'backfill_progress',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('migration_name', sa.String(length=100), nullable=False, unique=True),
        sa.Column('last_processed_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('batch_size', sa.Integer(), nullable=False, server_default='500'),
        sa.Column('rows_processed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_completed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('backfill_progress')
    op.drop_table('cost_meter')
    op.drop_table('dead_letter')
    op.drop_table('run_ledger')
    op.drop_table('review_items')
    op.drop_table('field_features')
    op.drop_table('fields')
    op.drop_table('extraction_versions')
    op.drop_table('pages')
    op.drop_table('documents')
