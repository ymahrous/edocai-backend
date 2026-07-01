from sqlmodel import SQLModel, Field
from typing import Optional, Dict, Any
from sqlalchemy import Column, JSON
from datetime import datetime, timezone
import uuid
from sqlalchemy import UniqueConstraint, Column, String
from pydantic import BaseModel # <-- ADD THIS IMPORT

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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    owner_id: str = Field(foreign_key="user.id", index=True)

class Extraction(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    document_id: str = Field(foreign_key="document.id")
    extracted_data: Dict[str, Any] = Field(default={}, sa_column=Column(JSON)) # <--- CHANGE THIS LINE
    confidence_score: float = 0.0

# --- NEW BILLING MODELS ---
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