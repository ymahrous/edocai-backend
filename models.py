import uuid
from pydantic import BaseModel, field_validator
from sqlmodel import SQLModel, Field, Relationship
from typing import List
from typing import Optional, Dict, Any
from sqlalchemy import Column, JSON, Text
from datetime import datetime, timezone, timedelta
from sqlalchemy import UniqueConstraint, Column, String, Boolean, Integer, DateTime, func

class CheckoutRequest(BaseModel):
    priceId: str

class User(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str
    plan: str = Field(default="free", sa_column=Column(String, default="free", server_default="free", nullable=False))
    base_currency: str = Field(
        default="USD",
        sa_column=Column(String(3), default="USD", server_default="USD", nullable=False)
    )
    # added for admin dashboard analytics
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

class Document(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    filename: str
    s3_url: str
    status: str = "PENDING"
    quickbooks_synced: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    owner_id: str = Field(foreign_key="user.id", index=True)
    flags: Optional[str] = None # e.g.: possible_duplicate, price_anomaly

class Extraction(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    document_id: str = Field(foreign_key="document.id")
    extracted_data: Dict[str, Any] = Field(default={}, sa_column=Column(JSON))
    confidence_score: float = 0.0
    category: Optional[str] = Field(default=None)
    # NEW
    vendor_id: Optional[str] = Field(default=None, foreign_key="vendor.id", index=True)
    vendor: Optional["Vendor"] = Relationship()
    # NEW: Currency fields
    original_currency: Optional[str] = Field(default=None, sa_column=Column(String(3)))  # ISO 4217
    original_amount: Optional[float] = Field(default=None)
    converted_amount: Optional[float] = Field(default=None)
    # Currency `converted_amount` is actually denominated in — a snapshot of the
    # user's base_currency AT PROCESSING TIME. Do not assume this equals the
    # user's *current* base_currency; they can differ if the user changed it since.
    converted_currency: Optional[str] = Field(default=None, sa_column=Column(String(3)))
    exchange_rate: Optional[float] = Field(default=None)

# --- BILLING MODELS ---
class Subscription(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True)
    plan: str = "free" # 'free' or 'pro'
    stripe_customer_id: Optional[str] = Field(default=None, unique=True)
    stripe_subscription_id: Optional[str] = Field(default=None, unique=True)
    status: str = "inactive" # 'active', 'past_due', 'canceled', etc.
    current_period_end: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class UsageRecord(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    month: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(day=1))
    documents_processed: int = 0
    # SQLModel trick to add a composite UniqueConstraint (one record per user per month)
    __table_args__ = (UniqueConstraint("user_id", "month"),)

class QuickBooksConnection(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True)
    realm_id: str = Field(index=True)
    access_token: Optional[str] = Field(default=None, sa_column=Column(Text))
    refresh_token: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Feedback(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: Optional[str] = Field(default=None, foreign_key="user.id", index=True) # Optional if logged out
    type: str = "Suggestion"
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Vendor(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    canonical_name: str = Field(index=True) # e.g., "Amazon"
    aliases: List[str] = Field(default=[], sa_column=Column(JSON)) # e.g., ["AMZN", "Amazon.com"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("user_id", "canonical_name"),)

class VendorRead(BaseModel):
    id: str
    canonical_name: str
    aliases: List[str]

    class Config:
        from_attributes = True

class ExtractionWithVendor(BaseModel):
    id: str
    document_id: str
    extracted_data: Dict[str, Any]
    confidence_score: float
    category: Optional[str] = None
    vendor_id: Optional[str] = None
    # NEW
    vendor: Optional[VendorRead] = None # The nested vendor object!
    # NEW: Currency fields
    original_currency: Optional[str] = None
    original_amount: Optional[float] = None
    converted_amount: Optional[float] = None
    converted_currency: Optional[str] = None  # currency converted_amount is actually in
    exchange_rate: Optional[float] = None

    class Config:
        from_attributes = True

class PasswordResetToken(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    token: str = Field(unique=True, index=True)
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=1))
    used: bool = Field(default=False, sa_column=Column(Boolean, default=False, server_default="false", nullable=False))


# NEW: Pydantic models for settings endpoint
class UserSettingsUpdate(BaseModel):
    base_currency: Optional[str] = None

    @field_validator("base_currency", mode="before")
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Common ISO 4217 codes - extend as needed
        valid_currencies = {
            "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY", "INR", "BRL",
            "MXN", "SGD", "HKD", "NZD", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF",
            "ILS", "THB", "MYR", "PHP", "IDR", "VND", "KRW", "TWD", "ZAR", "AED",
            "SAR", "QAR", "KWD", "BHD", "OMR", "JOD", "EGP", "NGN", "KES", "GHS"
        }
        v_upper = v.upper()
        if v_upper not in valid_currencies:
            raise ValueError(f"Invalid currency code. Must be a valid ISO 4217 code.")
        return v_upper

class UserSettingsRead(BaseModel):
    id: str
    username: str
    plan: str
    base_currency: str
    created_at: datetime

    class Config:
        from_attributes = True