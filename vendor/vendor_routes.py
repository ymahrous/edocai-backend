import database, models
from typing import List
from pydantic import BaseModel
from sqlmodel import Session, select
from dependencies import get_current_user
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/api/v1/vendors", tags=["vendors"])

@router.get("/", response_model=List[models.Vendor])
def get_vendors(
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Fetch all vendors for the current user"""
    vendors = session.exec(
        select(models.Vendor).where(models.Vendor.user_id == current_user.id)
    ).all()
    return vendors

class RenameVendorRequest(BaseModel):
    new_name: str

@router.patch("/{vendor_id}/rename")
def rename_vendor(
    vendor_id: str,
    req: RenameVendorRequest,
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Rename the canonical name of a vendor"""
    vendor = session.get(models.Vendor, vendor_id)
    if not vendor or vendor.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # Add the old canonical name to aliases so we still match it on future uploads
    if vendor.canonical_name not in vendor.aliases:
        vendor.aliases.append(vendor.canonical_name)
        
    vendor.canonical_name = req.new_name
    session.add(vendor)
    session.commit()
    return vendor

class MergeVendorsRequest(BaseModel):
    target_vendor_id: str

@router.post("/{source_vendor_id}/merge")
def merge_vendors(
    source_vendor_id: str,
    req: MergeVendorsRequest,
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Merges source vendor into target vendor. Target survives."""
    source = session.get(models.Vendor, source_vendor_id)
    target = session.get(models.Vendor, req.target_vendor_id)

    if not source or not target or source.user_id != current_user.id or target.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Vendor(s) not found")

    # 1. Move all aliases from source to target
    for alias in source.aliases:
        if alias not in target.aliases and alias.lower() != target.canonical_name.lower():
            target.aliases.append(alias)
    
    # 2. Add source canonical name to target aliases
    if source.canonical_name not in target.aliases and source.canonical_name.lower() != target.canonical_name.lower():
        target.aliases.append(source.canonical_name)

    # 3. Re-assign all Extractions linked to source over to target
    extractions = session.exec(
        select(models.Extraction).where(models.Extraction.vendor_id == source.id)
    ).all()
    for ext in extractions:
        ext.vendor_id = target.id
        session.add(ext)

    # 4. Delete the source vendor
    session.delete(source)
    session.commit()
    session.refresh(target)
    
    return {"message": "Vendors merged successfully", "surviving_vendor": target}