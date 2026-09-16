"""Tests for multi-currency support"""
import pytest
from sqlmodel import Session, select
from models import User, Extraction, UserSettingsUpdate, UserSettingsRead, ExtractionWithVendor
from auth import get_password_hash


def test_user_has_base_currency_field():
    """Test that User model has base_currency field with default USD"""
    user = User(
        username="test@test.com",
        hashed_password=get_password_hash("password123")
    )
    assert user.base_currency == "USD"


def test_user_base_currency_can_be_set():
    """Test that User base_currency can be set to different ISO 4217 codes"""
    user = User(
        username="test@test.com",
        hashed_password=get_password_hash("password123"),
        base_currency="EUR"
    )
    assert user.base_currency == "EUR"


def test_extraction_has_currency_fields():
    """Test that Extraction model has all new currency fields"""
    extraction = Extraction(
        document_id="doc-123",
        extracted_data={},
        confidence_score=0.95,
        category="Meals",
        original_currency="EUR",
        original_amount=100.0,
        converted_amount=108.50,
        exchange_rate=1.085
    )
    assert extraction.original_currency == "EUR"
    assert extraction.original_amount == 100.0
    assert extraction.converted_amount == 108.50
    assert extraction.exchange_rate == 1.085


def test_extraction_currency_fields_optional():
    """Test that Extraction currency fields are optional (for backward compatibility)"""
    extraction = Extraction(
        document_id="doc-123",
        extracted_data={},
        confidence_score=0.95,
        category="Meals"
    )
    assert extraction.original_currency is None
    assert extraction.original_amount is None
    assert extraction.converted_amount is None
    assert extraction.exchange_rate is None


class TestUserSettingsUpdate:
    """Tests for UserSettingsUpdate Pydantic model"""

    def test_valid_currency_codes(self):
        """Test that valid ISO 4217 codes are accepted"""
        valid_currencies = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "CNY", "INR", "BRL"]
        for currency in valid_currencies:
            settings = UserSettingsUpdate(base_currency=currency)
            assert settings.base_currency == currency.upper()

    def test_lowercase_currency_normalized(self):
        """Test that lowercase currency codes are normalized to uppercase"""
        settings = UserSettingsUpdate(base_currency="eur")
        assert settings.base_currency == "EUR"

    def test_none_currency_allowed(self):
        """Test that None is allowed (partial update)"""
        settings = UserSettingsUpdate(base_currency=None)
        assert settings.base_currency is None

    def test_invalid_currency_rejected(self):
        """Test that invalid currency codes are rejected"""
        with pytest.raises(ValueError, match="Invalid currency code"):
            UserSettingsUpdate(base_currency="INVALID")

    def test_empty_string_rejected(self):
        """Test that empty string is rejected"""
        with pytest.raises(ValueError):
            UserSettingsUpdate(base_currency="")


class TestUserSettingsRead:
    """Tests for UserSettingsRead Pydantic model"""

    def test_from_user_model(self):
        """Test creating UserSettingsRead from User model"""
        from datetime import datetime, timezone
        user = User(
            id="user-123",
            username="test@test.com",
            hashed_password=get_password_hash("password123"),
            plan="pro",
            base_currency="EUR",
            created_at=datetime.now(timezone.utc)
        )

        settings = UserSettingsRead.from_orm(user)
        assert settings.id == "user-123"
        assert settings.username == "test@test.com"
        assert settings.plan == "pro"
        assert settings.base_currency == "EUR"


class TestExtractionWithVendor:
    """Tests for ExtractionWithVendor Pydantic model with currency fields"""

    def test_includes_currency_fields(self):
        """Test that ExtractionWithVendor includes currency fields"""
        extraction = ExtractionWithVendor(
            id="ext-123",
            document_id="doc-123",
            extracted_data={"vendor": "Test Vendor"},
            confidence_score=0.95,
            category="Meals",
            original_currency="GBP",
            original_amount=50.0,
            converted_amount=63.25,
            exchange_rate=1.265
        )
        assert extraction.original_currency == "GBP"
        assert extraction.original_amount == 50.0
        assert extraction.converted_amount == 63.25
        assert extraction.exchange_rate == 1.265

    def test_currency_fields_optional(self):
        """Test that currency fields are optional for backward compatibility"""
        extraction = ExtractionWithVendor(
            id="ext-123",
            document_id="doc-123",
            extracted_data={},
            confidence_score=0.95,
            category="Meals"
        )
        assert extraction.original_currency is None
        assert extraction.original_amount is None
        assert extraction.converted_amount is None
        assert extraction.exchange_rate is None


# Integration tests with database
def test_user_base_currency_persisted(db_session: Session):
    """Test that user's base_currency is persisted to database"""
    user = User(
        username="currency@test.com",
        hashed_password=get_password_hash("password123"),
        base_currency="JPY"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Fetch from database
    fetched = db_session.exec(select(User).where(User.username == "currency@test.com")).first()
    assert fetched is not None
    assert fetched.base_currency == "JPY"


def test_extraction_currency_fields_persisted(db_session: Session):
    """Test that extraction currency fields are persisted to database"""
    user = User(
        username="extraction@test.com",
        hashed_password=get_password_hash("password123"),
        base_currency="USD"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    from models import Document
    document = Document(
        filename="test.pdf",
        s3_url="https://example.com/test.pdf",
        status="COMPLETED",
        owner_id=user.id
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    extraction = Extraction(
        document_id=document.id,
        extracted_data={"vendor": "Test", "total_amount": "€100.00"},
        confidence_score=0.95,
        category="Meals",
        original_currency="EUR",
        original_amount=100.0,
        converted_amount=108.50,
        exchange_rate=1.085
    )
    db_session.add(extraction)
    db_session.commit()
    db_session.refresh(extraction)

    # Fetch from database
    fetched = db_session.exec(
        select(Extraction).where(Extraction.document_id == document.id)
    ).first()

    assert fetched is not None
    assert fetched.original_currency == "EUR"
    assert fetched.original_amount == 100.0
    assert fetched.converted_amount == 108.50
    assert fetched.exchange_rate == 1.085


def test_legacy_extraction_without_currency_fields(db_session: Session):
    """Test that legacy extractions without currency fields still work"""
    user = User(
        username="legacy@test.com",
        hashed_password=get_password_hash("password123"),
        base_currency="USD"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    from models import Document
    document = Document(
        filename="legacy.pdf",
        s3_url="https://example.com/legacy.pdf",
        status="COMPLETED",
        owner_id=user.id
    )
    db_session.add(document)
    db_session.commit()
    db_session.refresh(document)

    # Legacy extraction without currency fields
    extraction = Extraction(
        document_id=document.id,
        extracted_data={"vendor": "Legacy", "total_amount": "$50.00"},
        confidence_score=0.90,
        category="Software"
    )
    db_session.add(extraction)
    db_session.commit()
    db_session.refresh(extraction)

    fetched = db_session.exec(
        select(Extraction).where(Extraction.document_id == document.id)
    ).first()

    assert fetched is not None
    assert fetched.original_currency is None
    assert fetched.original_amount is None
    assert fetched.converted_amount is None
    assert fetched.exchange_rate is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])