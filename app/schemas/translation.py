from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class TranslateProjectRequest(BaseModel):
    project_id: int
    genre: str = "cophong"
    batch_size: Optional[int] = None
    max_chars: Optional[int] = None
    provider: str = "gemini"
    model: Optional[str] = None

class TranslateBatchResponse(BaseModel):
    task_id: str
    status: str
    message: str
