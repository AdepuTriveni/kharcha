"""ORM models for tables created by Alembic. Keep in sync with migrations (a test checks)."""

import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Text, Time
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kharcha_common.db import Base

TZ = TIMESTAMP(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    firebase_uid: Mapped[str | None] = mapped_column(Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, server_default="Asia/Kolkata")
    roast_level: Mapped[str] = mapped_column(Text, server_default="MEDIUM")
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    experiment_group: Mapped[str | None] = mapped_column(Text)
    ml_consent: Mapped[bool] = mapped_column(Boolean, server_default="false")
    quiet_start: Mapped[time] = mapped_column(Time, server_default="22:00")
    quiet_end: Mapped[time] = mapped_column(Time, server_default="08:00")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default="now()")


class RawEventRow(Base):
    __tablename__ = "raw_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(Text)
    source_app: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    device_parse: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    posted_at: Mapped[datetime] = mapped_column(TZ)
    received_at: Mapped[datetime] = mapped_column(TZ, server_default="now()")


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    consumer: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(TZ, server_default="now()")


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    default_category: Mapped[str] = mapped_column(Text)


class MerchantAlias(Base):
    __tablename__ = "merchant_aliases"

    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(Text, ForeignKey("merchants.id"))
    source: Mapped[str] = mapped_column(Text)  # SEED|RULE|LLM|USER


class TransactionRow(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(Text)
    merchant_id: Mapped[str | None] = mapped_column(Text, ForeignKey("merchants.id"))
    merchant_raw: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    is_essential: Mapped[bool] = mapped_column(Boolean)
    reference_id: Mapped[str | None] = mapped_column(Text)
    account_hint: Mapped[str | None] = mapped_column(Text)
    balance_after_paise: Mapped[int | None] = mapped_column(BigInteger)
    txn_time: Mapped[datetime] = mapped_column(TZ)
    parse_method: Mapped[str] = mapped_column(Text)
    user_corrected: Mapped[bool] = mapped_column(Boolean, server_default="false")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default="now()")


class CashLedgerRow(Base):
    __tablename__ = "cash_ledger"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"))
    entry_type: Mapped[str] = mapped_column(Text)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    category: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    related_transaction_id: Mapped[str | None] = mapped_column(Text, ForeignKey("transactions.id"))
    occurred_at: Mapped[datetime] = mapped_column(TZ)
    prompted: Mapped[bool] = mapped_column(Boolean, server_default="false")


class Budget(Base):
    __tablename__ = "budgets"

    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"), primary_key=True)
    category: Mapped[str] = mapped_column(Text, primary_key=True)
    monthly_limit_paise: Mapped[int] = mapped_column(BigInteger)


class AlertSent(Base):
    __tablename__ = "alerts_sent"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"))
    alert_type: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    agent_run_id: Mapped[str | None] = mapped_column(Text)
    nudge_decision_id: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(TZ)
    status: Mapped[str] = mapped_column(Text)


class TransactionSource(Base):
    __tablename__ = "transaction_sources"

    transaction_id: Mapped[str] = mapped_column(
        Text, ForeignKey("transactions.id"), primary_key=True
    )
    raw_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_events.event_id"), primary_key=True
    )


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    suite: Mapped[str] = mapped_column(Text)  # PARSER|ASK|AGENT|FORECAST|RISK
    subject: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    git_sha: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default="now()")


class ParseRule(Base):
    __tablename__ = "parse_rules"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    sender_pattern: Mapped[str | None] = mapped_column(Text)  # sender key, e.g. HDFCBK
    regex: Mapped[str] = mapped_column(Text)
    field_map: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text)  # CANDIDATE|ACTIVE|DISABLED
    origin: Mapped[str] = mapped_column(Text)  # SEED|SYNTHESIZED
    match_count: Mapped[int] = mapped_column(Integer, server_default="0")
    mismatch_count: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(TZ, server_default="now()")


class UserMerchantOverride(Base):
    __tablename__ = "user_merchant_overrides"

    user_id: Mapped[str] = mapped_column(Text, ForeignKey("users.id"), primary_key=True)
    alias: Mapped[str] = mapped_column(Text, primary_key=True)  # normalize_merchant(raw)
    merchant_id: Mapped[str | None] = mapped_column(Text, ForeignKey("merchants.id"))
    category: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(TZ, server_default="now()")
