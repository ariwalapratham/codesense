# CodeSense Implementation Documentation

## File Structure

```
codesense/
├── backend/
│   ├── main.py                          # FastAPI application entry point
│   ├── requirements.txt                  # Python dependencies
│   │
│   ├── api/                              # API routes
│   │   ├── __init__.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── ingestion.py              # Repository ingestion endpoints
│   │       ├── repository.py             # Repository management endpoints
│   │       └── chat.py                   # Chat/search endpoints
│   │
│   ├── config/                           # Configuration management
│   │   ├── __init__.py
│   │   └── settings.py                    # Pydantic settings with environment variables
│   │
│   ├── database/                         # Database connections
│   │   ├── __init__.py
│   │   └── mongodb.py                    # MongoDB connection (placeholder)
│   │
│   ├── models/                           # Data models
│   │   ├── __init__.py
│   │   ├── schemas.py                    # Pydantic request/response models
│   │   └── database.py                   # Database models (placeholder)
│   │
│   ├── services/                         # Business logic services
│   │   ├── __init__.py
│   │   ├── ingestion_service.py          # Orchestrates ingestion pipeline
│   │   ├── repository_service.py         # Git repository cloning
│   │   ├── parser_service.py             # Code parsing with Tree-sitter
│   │   ├── embedding_service.py          # Chunking and embedding generation
│   │   ├── vector_store_service.py       # FAISS vector store management
│   │   ├── retrieval_service.py          # Retrieval service (placeholder)
│   │   └── llm_service.py                # LLM service (placeholder)
│   │
│   └── utils/                            # Utility modules
│       ├── __init__.py
│       ├── chunker.py                    # Chunking utilities (legacy)
│       └── code_parser.py                # Code parsing utilities (legacy)
│
├── env/                                  # Python virtual environment
├── temp_repos/                           # Temporary cloned repositories
├── vector_stores/                        # FAISS indices and mappings
│   ├── {repo_id}.faiss                   # FAISS index files
│   └── {repo_id}.pkl                     # Chunk ID to index position mappings
├── README.md
└── LICENSE
```

## Implemented Components

### 1. FastAPI Application (`backend/main.py`)

**What's Implemented:**
- FastAPI application with CORS middleware
- Lifespan context manager for startup/shutdown
- Root and health check endpoints
- Router registration for ingestion and repository routes
- Logging configuration

**Features:**
- Application title: "CodeSense API"
- Version: 1.0.0
- CORS enabled for all origins (configurable)
- Uvicorn server with auto-reload

---

### 2. Configuration Management (`backend/config/settings.py`)

**What's Implemented:**
- Pydantic Settings class for environment variable management
- Configuration for:
  - **Gemini API**: API key, embedding model (`gemini-embedding-001`), LLM model
  - **MongoDB**: Connection URL, database name
  - **Vector Store**: Storage path for FAISS indices
  - **Repository Processing**: Temporary repository storage path
  - **Git Authentication**: GitHub and GitLab tokens (optional)
- Automatic loading from `.env` file
- Case-insensitive environment variable matching

---

### 3. API Routes

#### 3.1 Ingestion Routes (`backend/api/routes/ingestion.py`)

**What's Implemented:**
- `POST /ingestion/ingest` - Repository ingestion endpoint
  - Accepts repository URL, branch (optional), cleanup flag
  - Returns ingestion status and metadata
  - Lazy initialization of ingestion service
- `GET /ingestion/health` - Health check for ingestion service

**Request Model:**
- `repo_url`: Repository URL (required)
- `branch`: Specific branch to clone (optional)
- `cleanup`: Whether to cleanup temporary repo after processing (default: true)

**Response Model:**
- `success`: Boolean status
- `message`: Status message
- `metadata`: Ingestion statistics and repository info

#### 3.2 Repository Routes (`backend/api/routes/repository.py`)

**What's Implemented:**
- Repository management endpoints (structure exists)

#### 3.3 Chat Routes (`backend/api/routes/chat.py`)

**What's Implemented:**
- Chat/search endpoints (structure exists)

---

### 4. Services

#### 4.1 Ingestion Service (`backend/services/ingestion_service.py`)

**What's Implemented:**
- Complete ingestion pipeline orchestration:
  1. **Repository Information Extraction**: Parses URL to extract owner, repo name, and generate repo_id
  2. **Repository Cloning**: Clones repository to temporary directory with authentication support
  3. **Commit Hash Extraction**: Gets current commit hash from cloned repository
  4. **Code File Discovery**: Discovers code files based on language extensions
  5. **Code Parsing**: Parses files to extract functions, classes, and imports
  6. **Chunking and Embedding**: Chunks code and generates embeddings
  7. **Vector Store Storage**: Saves embeddings to FAISS
  8. **Cleanup**: Removes temporary repository directory

**Key Methods:**
- `ingest_repository()`: Main orchestration method
- `_extract_repo_info()`: Extracts owner, repo_name, repo_id from URL
- `_prepare_temp_repo_path()`: Creates unique temporary directory path
- `_get_commit_hash()`: Extracts commit hash from Git repository
- `_discover_code_files()`: Finds code files in repository
- `_parse_files()`: Parses code files using ParserService
- `_store_embeddings()`: Stores embeddings in FAISS (MongoDB TODO)
- `_cleanup_temp_repo()`: Removes temporary repository

**Metadata Collected:**
- Repository information (owner, name, ID, URL, branch, commit hash)
- File processing statistics
- Parsing statistics (functions, classes, imports, languages)
- Chunk processing statistics
- Status tracking

---

#### 4.2 Repository Service (`backend/services/repository_service.py`)

**What's Implemented:**
- Git repository cloning with authentication
- Support for GitHub and GitLab
- Branch-specific cloning
- Shallow clone support (depth parameter)
- Repository information retrieval
- Pull/update functionality

**Key Methods:**
- `clone_repository()`: Clones repository with authentication
- `_detect_repository_type()`: Detects GitHub vs GitLab
- `_prepare_authenticated_url()`: Embeds token in URL for authentication
- `_get_token_for_repo()`: Gets appropriate token from settings
- `pull_repository()`: Pulls latest changes from remote
- `get_repository_info()`: Gets repository metadata

**Features:**
- Automatic token injection for authenticated cloning
- SSH to HTTPS URL conversion
- Error handling for authentication failures
- Repository existence checking

---

#### 4.3 Parser Service (`backend/services/parser_service.py`)

**What's Implemented:**
- Multi-language code parsing using Tree-sitter
- Function extraction from AST
- Class extraction from AST
- Import statement extraction
- Support for 20+ programming languages

**Supported Languages:**
- Python, JavaScript, TypeScript, Java, Go, C/C++, Ruby, PHP, C#, Rust, Swift, Kotlin, Scala, R, Bash, SQL, HTML, CSS, JSON, YAML, TOML, XML, Markdown

**Key Methods:**
- `parse_file()`: Main parsing method
- `_get_language()`: Maps file extension to Tree-sitter language
- `_get_parser()`: Gets or creates parser for language
- `_extract_functions()`: Extracts function definitions
- `_extract_classes()`: Extracts class definitions
- `_extract_imports()`: Extracts import statements
- `_find_nodes_by_types()`: Finds AST nodes by type
- `_get_node_text()`: Extracts text from AST node

**Extraction Configuration:**
- Language-specific configuration for function/class patterns
- Node type mappings for different languages
- Handles nested structures (e.g., C++ function_declarator)
- Generic fallback for unsupported languages

**Extracted Data Structure:**
```python
{
    "file_path": str,
    "language": str,
    "code_text": str,
    "functions": [
        {
            "name": str,
            "signature": str,
            "body": str,
            "start_line": int,
            "end_line": int
        }
    ],
    "classes": [
        {
            "name": str,
            "body": str,
            "start_line": int,
            "end_line": int
        }
    ],
    "imports": [
        {
            "text": str,
            "line": int
        }
    ]
}
```

---

#### 4.4 Embedding Service (`backend/services/embedding_service.py`)

**What's Implemented:**
- Hybrid chunking strategy:
  - Semantic chunks for functions/classes (if within size limit)
  - Recursive splitting for large functions/classes
  - Module-level code extraction and chunking
  - Fallback chunking for files without functions/classes
- Embedding generation using Google Gemini API
- Batch processing with configurable batch size
- Embedding validation (NaN/Inf checks, dimension validation)

**Key Methods:**
- `chunk_code()`: Creates chunks from parsed structures
- `chunk_and_embed()`: Convenience method for chunking + embedding
- `generate_embeddings()`: Generates embeddings for chunks in batches
- `_create_chunk()`: Creates standardized chunk dictionary
- `_extract_module_level_code()`: Extracts code outside functions/classes
- `_estimate_tokens()`: Estimates token count from text

**Chunking Strategy:**
- **Semantic Chunks**: Functions and classes as single chunks (if < 3200 chars)
- **Recursive Splitting**: Large functions/classes split using RecursiveCharacterTextSplitter
- **Module-level Code**: Code outside functions/classes extracted and chunked
- **Fallback**: Entire file chunked if no functions/classes found

**Chunk Structure:**
```python
{
    "chunk_id": str,
    "file_path": str,
    "function_name": Optional[str],
    "class_name": Optional[str],
    "code_text": str,
    "start_line": int,
    "end_line": int,
    "language": str,
    "metadata": {
        "type": "function" | "class" | "module_level" | "file",
        "signature": Optional[str],
        "is_split": bool,
        "split_index": Optional[int],
        "total_splits": Optional[int]
    },
    "embedding": Optional[List[float]]  # Added after embedding generation
}
```

**Embedding Configuration:**
- Model: `gemini-embedding-001`
- Output dimensionality: 768 (requested, default is 3072)
- Batch size: Configurable (default: 10 chunks per batch)
- Task type: Not specified (uses default)
- Uses `google-genai` SDK (new SDK, not `google-generativeai`)

**Text Splitter Configuration:**
- Chunk size: 2500 characters (~625 tokens)
- Chunk overlap: 200 characters (~50 tokens)
- Separators: Triple newlines, double newlines, single newlines, spaces, characters

---

#### 4.5 Vector Store Service (`backend/services/vector_store_service.py`)

**What's Implemented:**
- FAISS index management (local file storage)
- Index creation (IndexFlatL2 for exact search)
- Index loading and saving
- Embedding normalization (L2 normalization)
- Chunk ID to index position mapping
- Similarity search functionality

**Key Methods:**
- `save_embeddings()`: Saves embeddings to FAISS index
- `search_similar()`: Searches for similar chunks
- `_create_index()`: Creates new FAISS index
- `_load_index()`: Loads index from disk
- `_save_index()`: Saves index to disk
- `_get_or_create_index()`: Gets existing or creates new index
- `_load_chunk_mapping()`: Loads chunk_id to index mapping
- `_save_chunk_mapping()`: Saves chunk_id to index mapping

**Storage:**
- FAISS indices: `{vector_store_path}/{repo_id}.faiss`
- Chunk mappings: `{vector_store_path}/{repo_id}.pkl`
- In-memory caching of loaded indices

**Features:**
- Embedding normalization for better L2 distance performance
- Dimension validation (accepts 768 or 3072)
- NaN/Inf embedding validation
- Support for appending to existing indices
- Overwrite mode for index replacement

**Index Type:**
- Uses `IndexFlatL2` for exact similarity search
- Supports `IndexIVFFlat` for large datasets (not currently used)

---

### 5. Data Models

#### 5.1 API Schemas (`backend/models/schemas.py`)

**What's Implemented:**
- `IngestionRequest`: Request model for repository ingestion
  - `repo_url`: HttpUrl (required)
  - `branch`: Optional[str]
  - `cleanup`: bool (default: True)
- `IngestionResponse`: Response model for ingestion
  - `success`: bool
  - `message`: str
  - `metadata`: Optional[dict]

---

### 6. Database

#### 6.1 MongoDB (`backend/database/mongodb.py`)

**What's Implemented:**
- Placeholder file (empty, ready for implementation)

#### 6.2 Database Models (`backend/models/database.py`)

**What's Implemented:**
- Placeholder file (empty, ready for implementation)

---

### 7. Utilities

#### 7.1 Chunker (`backend/utils/chunker.py`)

**What's Implemented:**
- Legacy chunking utilities (may be deprecated in favor of EmbeddingService)

#### 7.2 Code Parser (`backend/utils/code_parser.py`)

**What's Implemented:**
- Legacy parsing utilities (may be deprecated in favor of ParserService)

---

## Dependencies

**Core Framework:**
- `fastapi==0.121.0` - Web framework
- `uvicorn[standard]==0.38.0` - ASGI server
- `python-multipart==0.0.20` - Form data handling

**AI/ML:**
- `langchain==1.0.5` - LangChain framework
- `langchain-text-splitters==1.0.0` - Text splitting utilities
- `langchain-google-genai==3.0.1` - LangChain Google GenAI integration
- `google-genai==1.49.0` - Google GenAI SDK (new SDK)

**Code Parsing:**
- `tree-sitter==0.20.4` - Tree-sitter parser library
- `tree-sitter-languages==1.10.2` - Multi-language Tree-sitter support

**Vector Store:**
- `faiss-cpu==1.12.0` - FAISS vector similarity search
- `numpy==2.3.4` - Numerical computing

**Database:**
- `pymongo==4.15.3` - MongoDB driver

**Git Operations:**
- `gitpython==3.1.45` - Git repository operations

**Configuration:**
- `pydantic==2.12.4` - Data validation
- `pydantic-settings==2.11.0` - Settings management
- `python-dotenv==1.2.1` - Environment variable loading

---

## Current Pipeline Flow

1. **API Request** → `POST /ingestion/ingest` with repository URL
2. **Repository Cloning** → RepositoryService clones repo to temp directory
3. **File Discovery** → IngestionService discovers code files
4. **Code Parsing** → ParserService extracts functions, classes, imports
5. **Chunking** → EmbeddingService creates semantic chunks
6. **Embedding Generation** → EmbeddingService generates embeddings via Gemini API
7. **Vector Storage** → VectorStoreService saves embeddings to FAISS
8. **Cleanup** → Temporary repository removed (if cleanup=True)

---

## Storage Locations

- **Temporary Repositories**: `{temp_repos_path}/{repo_id}_{timestamp}/`
- **FAISS Indices**: `{vector_store_path}/{repo_id}.faiss`
- **Chunk Mappings**: `{vector_store_path}/{repo_id}.pkl`
- **Vector Store Path**: Configurable via `settings.vector_store_path` (default: `../vector_stores`)
- **Temp Repos Path**: Configurable via `settings.temp_repos_path` (default: `../temp_repos`)

---

## Configuration

All configuration is managed through environment variables loaded from `.env` file:

- `GEMINI_API_KEY`: Google Gemini API key
- `MONGODB_URL`: MongoDB connection string (default: `mongodb://localhost:27017`)
- `DB_NAME`: Database name (default: `codesense`)
- `GITHUB_TOKEN`: GitHub personal access token (optional)
- `GITLAB_TOKEN`: GitLab personal access token (optional)

Other settings use defaults defined in `settings.py`.

---

## Notes

- MongoDB integration is prepared but not yet implemented (placeholder files exist)
- Embeddings are stored in FAISS, not MongoDB (embeddings are large and not queryable)
- Uses new `google-genai` SDK (not the deprecated `google-generativeai`)
- Embedding dimension: 768 (requested from API, default would be 3072)
- Supports free tier Gemini API with rate limit handling
- Lazy initialization of services for better startup performance

