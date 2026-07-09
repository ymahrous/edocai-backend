import uuid
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Relationship
from typing import List
from typing import Optional, Dict, Any
from sqlalchemy import Column, JSON, Text
from datetime import datetime, timezone, timedelta
from sqlalchemy import UniqueConstraint, Column, String, Boolean, Integer

class CheckoutRequest(BaseModel):
    priceId: str

class User(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str
    plan: str = Field(sa_column=Column(String, default="free", server_default="free", nullable=False))

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
    vendor_id: Optional[str] = Field(default=None, foreign_key="vendor.id", index=True)
    vendor: Optional["Vendor"] = Relationship()

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
    vendor: Optional[VendorRead] = None # The nested vendor object!

    class Config:
        from_attributes = True

class PasswordResetToken(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    token: str = Field(unique=True, index=True)
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=1))
    used: bool = Field(default=False, sa_column=Column(Boolean, default=False, server_default="false", nullable=False))