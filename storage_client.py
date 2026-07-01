import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Initialize the Supabase client
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_ANON_KEY")
supabase: Client = create_client(url, key)

def upload_to_storage(file_bytes: bytes, filename: str) -> str:
    """Uploads file to Supabase and returns the public URL."""
    bucket_name = os.getenv("SUPABASE_BUCKET_NAME")
    
    # We add upsert="True" so if you upload the same file twice for testing, 
    # it just overwrites it instead of throwing a duplicate error.
    supabase.storage.from_(bucket_name).upload(
        path=filename,
        file=file_bytes,
        file_options={"content-type": "application/octet-stream", "upsert": "true"} 
    )
    
    # Get the public URL
    public_url = supabase.storage.from_(bucket_name).get_public_url(filename)
    
    return public_url

def delete_from_storage(filename: str) -> None:
    """Deletes a file from Supabase storage."""
    bucket_name = os.getenv("SUPABASE_BUCKET_NAME")
    supabase.storage.from_(bucket_name).remove([filename])