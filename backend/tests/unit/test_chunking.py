from services.embedding_service import EmbeddingService


def _structure(**overrides):
    base = {
        "file_path": "math.py",
        "code_text": "def add(a, b):\n    return a + b\n",
        "language": "python",
        "functions": [
            {
                "name": "add",
                "body": "    return a + b",
                "signature": "def add(a, b):",
                "start_line": 1,
                "end_line": 2,
            }
        ],
        "classes": [],
        "imports": [],
    }
    base.update(overrides)
    return base


def test_small_function_is_one_chunk():
    chunks = EmbeddingService().chunk_code([_structure()])
    func_chunks = [c for c in chunks if c["metadata"].get("type") == "function"]

    assert len(func_chunks) == 1
    chunk = func_chunks[0]
    assert chunk["chunk_id"]
    assert chunk["file_path"] == "math.py"
    assert chunk["language"] == "python"
    assert chunk["function_name"] == "add"
    assert chunk["start_line"] == 1
    assert chunk["end_line"] == 2
    assert not chunk["metadata"].get("is_split")


def test_oversized_function_splits():
    body = "print(1)\n" * 500
    chunks = EmbeddingService().chunk_code(
        [
            _structure(
                code_text=f"def big():\n{body}",
                functions=[
                    {
                        "name": "big",
                        "body": body,
                        "signature": "def big():",
                        "start_line": 1,
                        "end_line": body.count("\n") + 1,
                    }
                ],
            )
        ]
    )
    split = [c for c in chunks if c["metadata"].get("is_split")]

    assert len(split) > 1
    assert all(c["function_name"] == "big" for c in split)
    assert all(c["language"] == "python" for c in split)
    assert all(c["chunk_id"] for c in split)
    assert all(c["start_line"] >= 1 and c["end_line"] >= c["start_line"] for c in split)


def test_module_level_is_extracted():
    header = "CONSTANT = " + "a" * 120
    func = "def add(a, b):\n    return a + b"
    chunks = EmbeddingService().chunk_code(
        [
            _structure(
                code_text=f"{header}\n\n{func}\n",
                functions=[
                    {
                        "name": "add",
                        "body": "    return a + b",
                        "signature": "def add(a, b):",
                        "start_line": 3,
                        "end_line": 4,
                    }
                ],
            )
        ]
    )
    module_chunks = [c for c in chunks if c["metadata"].get("type") == "module_level"]
    func_chunks = [c for c in chunks if c["metadata"].get("type") == "function"]

    assert len(func_chunks) == 1
    assert len(module_chunks) >= 1
    assert header in module_chunks[0]["code_text"]
    assert module_chunks[0]["chunk_id"]
    assert module_chunks[0]["language"] == "python"
    assert module_chunks[0]["start_line"] >= 1
    assert module_chunks[0]["end_line"] >= module_chunks[0]["start_line"]
