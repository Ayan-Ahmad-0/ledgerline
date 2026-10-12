import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional, Any
from decimal import Decimal

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    Index,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.orm import (
    declarative_base,
    relationship,
    sessionmaker,
)

from app.core.config import get_settings

Base = declarative_base()


# -------------------------------------------------------------------------
# Core Document & Page Models
# -------------------------------------------------------------------------

class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_hash = Column(String(64), nullable=False, index=True)
    r2_storage_key = Column(String(512), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    mime_type = Column(String(100), nullable=False)
    page_count = Column(Integer, default=0, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    pages = relationship("Page", back_populates="document", cascade="all, delete-orphan")
    extraction_versions = relationship("ExtractionVersion", back_populates="document")
    review_items = relationship("ReviewItem", back_populates="document")

    __table_args__ = (
        Index("ix_documents_file_hash", "file_hash"),
    )


class Page(Base):
    __tablename__ = "pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    page_number = Column(Integer, nullable=False)
    page_hash = Column(String(64), nullable=False, index=True)
    is_native_text = Column(Boolean, default=True, nullable=False)
    raw_text = Column(Text, nullable=True)
    layout_metadata = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    document = relationship("Document", back_populates="pages")
    fields = relationship("Field", back_populates="page")

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_page_number"),
        Index("ix_pages_page_hash", "page_hash"),
    )


# -------------------------------------------------------------------------
# Temporal Extraction Versions (Append-Only Time Travel)
# -------------------------------------------------------------------------

class ExtractionVersion(Base):
    """
    Append-only extraction versions with valid_from and valid_to
    to support temporal as_of queries.
    """
    __tablename__ = "extraction_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    extractor_version = Column(String(50), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    idempotency_key = Column(String(128), nullable=False, index=True)

    valid_from = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )
    valid_to = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True
    )
    is_current = Column(Boolean, default=True, nullable=False, index=True)
    metadata_json = Column(JSONB, nullable=True)

    document = relationship("Document", back_populates="extraction_versions")
    fields = relationship("Field", back_populates="extraction_version", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_extraction_versions_time_travel", "document_id", "valid_from", "valid_to"),
        Index("ix_extraction_versions_idempotency", "idempotency_key"),
    )


# -------------------------------------------------------------------------
# Fields & Field Features
# -------------------------------------------------------------------------

class Field(Base):
    __tablename__ = "fields"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    extraction_version_id = Column(
        UUID(as_uuid=True), ForeignKey("extraction_versions.id", ondelete="CASCADE"), nullable=False
    )
    page_id = Column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL"), nullable=True
    )
    field_name = Column(String(100), nullable=False, index=True)
    raw_value = Column(Text, nullable=True)
    normalized_value = Column(JSONB, nullable=True)
    confidence_raw = Column(Float, nullable=False, default=0.0)
    confidence_calibrated = Column(Float, nullable=False, default=0.0)
    bounding_box = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    extraction_version = relationship("ExtractionVersion", back_populates="fields")
    page = relationship("Page", back_populates="fields")
    features = relationship("FieldFeature", back_populates="field", uselist=False, cascade="all, delete-orphan")
    review_item = relationship("ReviewItem", back_populates="field", uselist=False)


class FieldFeature(Base):
    __tablename__ = "field_features"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    field_id = Column(
        UUID(as_uuid=True), ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    ocr_char_confidence = Column(Float, nullable=True)
    token_probability = Column(Float, nullable=True)
    layout_feature_vector = Column(JSONB, nullable=True)
    math_validation_passed = Column(Boolean, default=True, nullable=False)
    two_pass_agreement = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    field = relationship("Field", back_populates="features")


# -------------------------------------------------------------------------
# Review Items (Row-Level Leases & Optimistic Concurrency)
# -------------------------------------------------------------------------

class ReviewItem(Base):
    """
    Supports row-level leases using SELECT FOR UPDATE SKIP LOCKED
    and optimistic concurrency with version integer.
    """
    __tablename__ = "review_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    field_id = Column(
        UUID(as_uuid=True), ForeignKey("fields.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    version = Column(Integer, default=1, nullable=False)
    status = Column(String(50), default="pending", nullable=False, index=True)
    leased_by = Column(String(100), nullable=True)
    leased_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    proposed_value = Column(JSONB, nullable=True)
    reviewed_value = Column(JSONB, nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    document = relationship("Document", back_populates="review_items")
    field = relationship("Field", back_populates="review_item")

    __table_args__ = (
        Index("ix_review_items_lease_state", "status", "lease_expires_at"),
    )


# -------------------------------------------------------------------------
# Pipeline Spine: Run Ledger & Dead Letter
# -------------------------------------------------------------------------

class RunLedger(Base):
    __tablename__ = "run_ledger"

    run_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="running", nullable=False)
    watermark = Column(String(255), nullable=True)
    documents_processed = Column(Integer, default=0, nullable=False)
    documents_parked = Column(Integer, default=0, nullable=False)
    canary_f1_score = Column(Float, nullable=True)
    baseline_f1_score = Column(Float, nullable=True)
    error_summary = Column(JSONB, nullable=True)


class DeadLetter(Base):
    __tablename__ = "dead_letter"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), nullable=True)
    file_hash = Column(String(64), nullable=True, index=True)
    failure_reason = Column(String(100), nullable=False)
    error_details = Column(JSONB, nullable=False)
    parked_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    resolved = Column(Boolean, default=False, nullable=False)
    resolution_notes = Column(Text, nullable=True)


# -------------------------------------------------------------------------
# Cost Meter
# -------------------------------------------------------------------------

class CostMeter(Base):
    __tablename__ = "cost_meter"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), nullable=True)
    page_id = Column(UUID(as_uuid=True), nullable=True)
    routing_path = Column(String(50), nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    estimated_cost_usd = Column(Numeric(10, 6), default=Decimal("0.000000"), nullable=False)
    latency_ms = Column(Integer, default=0, nullable=False)
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


# -------------------------------------------------------------------------
# Schema Backfill Progress Tracking Table
# -------------------------------------------------------------------------

class BackfillProgress(Base):
    __tablename__ = "backfill_progress"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    migration_name = Column(String(100), nullable=False, unique=True)
    last_processed_id = Column(UUID(as_uuid=True), nullable=True)
    batch_size = Column(Integer, default=500, nullable=False)
    rows_processed = Column(Integer, default=0, nullable=False)
    is_completed = Column(Boolean, default=False, nullable=False)
    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )


# -------------------------------------------------------------------------
# Lazy Engine & Session Factory (Driver-Resilient)
# -------------------------------------------------------------------------

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        try:
            _engine = create_async_engine(
                settings.DATABASE_URL,
                pool_size=settings.DB_POOL_SIZE,
                max_overflow=settings.DB_MAX_OVERFLOW,
                pool_timeout=settings.DB_POOL_TIMEOUT,
                echo=settings.DEBUG,
            )
        except Exception:
            # Fallback to in-memory async SQLite if Postgres driver not installed in current environment
            _engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=settings.DEBUG)
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
