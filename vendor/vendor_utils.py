from sqlmodel import Session, select
from models import Vendor
from difflib import get_close_matches
from typing import Optional

def match_or_create_vendor(session: Session, user_id: str, raw_vendor_string: str) -> Vendor:
    """
    Attempts to match a raw extracted vendor string to an existing Vendor.
    If no match, creates a new Vendor with the string as the canonical name.
    """
    if not raw_vendor_string or not raw_vendor_string.strip():
        return None

    raw_vendor_string = raw_vendor_string.strip()
    
    # 1. Fetch all vendors for this user to do local matching
    # (For V1, this is fine. For thousands of vendors, you'd use Postgres trigram/fuzzy matching)
    vendors = session.exec(select(Vendor).where(Vendor.user_id == user_id)).all()
    
    all_names = []
    name_to_vendor = {}
    for v in vendors:
        all_names.append(v.canonical_name.lower())
        name_to_vendor[v.canonical_name.lower()] = v
        for alias in v.aliases:
            all_names.append(alias.lower())
            name_to_vendor[alias.lower()] = v

    # 2. Try to find a close match (cutoff=0.8 means 80% similarity)
    matches = get_close_matches(raw_vendor_string.lower(), all_names, n=1, cutoff=0.8)
    
    if matches:
        matched_name = matches[0]
        existing_vendor = name_to_vendor[matched_name]
        
        # If the raw string matched but isn't in the aliases list, add it for future exact matching
        if raw_vendor_string.lower() not in [a.lower() for a in existing_vendor.aliases] and raw_vendor_string.lower() != existing_vendor.canonical_name.lower():
            existing_vendor.aliases.append(raw_vendor_string)
            session.add(existing_vendor)
            session.commit()
            session.refresh(existing_vendor)
            
        return existing_vendor

    # 3. No match found. Create a new Vendor record
    new_vendor = Vendor(
        user_id=user_id,
        canonical_name=raw_vendor_string, # User can clean this up later in UI
        aliases=[]
    )
    session.add(new_vendor)
    session.commit()
    session.refresh(new_vendor)
    
    return new_vendor