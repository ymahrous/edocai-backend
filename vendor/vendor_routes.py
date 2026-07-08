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
    return session.exec(select(models.Vendor).where(models.Vendor.user_id == current_user.id)).all()

class RenameVendorRequest(BaseModel):
    new_name: str

@router.patch("/{vendor_id}/rename")
def rename_vendor(
    vendor_id: str,
    req: RenameVendorRequest,
    session: Session = Depends(database.get_session),
    current_user: models.User = Depends(get_current_user)
):
    vendor = session.get(models.Vendor, vendor_id)
    if not vendor or vendor.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Vendor not found")

    old_name = vendor.canonical_name

    # Add old name to aliases if not already there
    if old_name not in vendor.aliases:
        vendor.aliases.append(old_name)
    
    vendor.canonical_name = req.new_name
    
    # FIX 1: Force SQLModel to see the JSON mutation by re-assigning the list
    vendor.aliases = vendor.aliases 
    
    session.add(vendor)

    # FIX 2: Update all linked Extractions' JSON data to reflect the new name
    extractions = session.exec(
        select(models.Extraction).where(models.Extraction.vendor_id == vendor.id)
    ).all()
    
    for ext in extractions:
        ext.extracted_data["vendor"] = req.new_name
        # Force SQLModel to see the JSON mutation
        ext.extracted_data = ext.extracted_data 
        session.add(ext)

    session.commit()
    session.refresh(vendor)
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
    source = session.get(models.Vendor, source_vendor_id)
    target = session.get(models.Vendor, req.target_vendor_id)

    if not source or not target or source.user_id != current_user.id or target.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Vendor(s) not found")

    # move aliases from source to target
    for alias in source.aliases:
        if alias not in target.aliases and alias.lower() != target.canonical_name.lower():
            target.aliases.append(alias)
    
    # add source canonical name to target aliases
    if source.canonical_name not in target.aliases and source.canonical_name.lower() != target.canonical_name.lower():
        target.aliases.append(source.canonical_name)

    # force SQLModel JSON mutation detection
    target.aliases = target.aliases
    session.add(target)

    # 2. re-assign Extractions to target AND update their JSON vendor string
    extractions = session.exec(
        select(models.Extraction).where(models.Extraction.vendor_id == source.id)
    ).all()
    for ext in extractions:
        ext.vendor_id = target.id
        ext.extracted_data["vendor"] = target.canonical_name
        ext.extracted_data = ext.extracted_data # force JSON mutation
        session.add(ext)

    # 3. Delete source
    session.delete(source)
    
    session.commit()
    session.refresh(target)
    
    return {"message": "Vendors merged successfully", "surviving_vendor": target}