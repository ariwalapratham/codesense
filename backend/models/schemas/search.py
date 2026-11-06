"""Pydantic schemas for repository search."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SearchHitResponse(BaseModel):
    chunk_id: str
    file_path: str
    function_name: Optional[str] = None
    class_name: Optional[str] = None
    language: str
    start_line: int
    end_line: int
    score: float
    code_text: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "chunk_id": "src/main.py_func_0",
                "file_path": "src/main.py",
                "function_name": "process_data",
                "class_name": None,
                "language": "python",
                "start_line": 10,
                "end_line": 12,
                "score": 0.82,
                "code_text": "def process_data(data):\n    return data * 2",
            }
        }
    )


class SearchResponse(BaseModel):
    results: List[SearchHitResponse] = Field(default_factory=list)
