"""Pydantic schema for chunk documents in MongoDB."""

from datetime import datetime
from typing import Optional
from bson import ObjectId

from pydantic import BaseModel, ConfigDict, Field

class ChunkMetadata(BaseModel):
    """Metadata for a code chunk."""

    type: str = Field(
        ...,
        description="Chunk type: function, class, module_level, or file"
    )
    signature: Optional[str] = Field(
        None, description="Function or class signature"
    )
    is_split: bool = Field(
        default=False, description="Whether chunk was split from larger code"
    )
    split_index: Optional[int] = Field(
        None, description="Index of split if chunk was split"
    )
    total_splits: Optional[int] = Field(
        None, description="Total number of splits if chunk was split"
    )


class ChunkSchema(BaseModel):
    """Schema for chunk documents in MongoDB."""

    chunk_id: str = Field(..., description="Unique chunk identifier")
    repo_id: ObjectId = Field(..., description="Reference to the repository document")
    repository_url: str = Field(..., description="Repository URL")
    file_path: str = Field(..., description="File path relative to repository")
    function_name: Optional[str] = Field(
        None, description="Function name if chunk is from a function"
    )
    class_name: Optional[str] = Field(
        None, description="Class name if chunk is from a class"
    )
    code_text: str = Field(..., description="Code content")
    language: str = Field(..., description="Programming language")
    start_line: int = Field(..., description="Starting line number")
    end_line: int = Field(..., description="Ending line number")
    embedding_index: int = Field(
        ..., description="Index position in FAISS vector store"
    )
    commit_hash: Optional[str] = Field(
        None, description="Commit hash when chunk was created"
    )
    metadata: ChunkMetadata = Field(
        ..., description="Additional chunk metadata"
    )
    created_at: datetime = Field(
        default_factory=datetime.now, description="Creation timestamp"
    )

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "chunk_id": "chunk_abc123",
                "repo_id": ObjectId("669999999999999999999999"),
                "repository_url": "https://github.com/owner/repo.git",
                "file_path": "src/main.py",
                "function_name": "process_data",
                "class_name": None,
                "code_text": "def process_data(data):\n    return data * 2",
                "language": "python",
                "start_line": 10,
                "end_line": 12,
                "embedding_index": 0,
                "commit_hash": "abc1234",
                "metadata": {
                    "type": "function",
                    "signature": "def process_data(data)",
                    "is_split": False,
                    "split_index": None,
                    "total_splits": None
                },
                "created_at": "2025-11-20 16:21:33.151743"
            }
        }
    )
