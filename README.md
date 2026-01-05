# CodeSense

AI-powered assistant that understands code semantics, not just keywords.

CodeSense is an intelligent code analysis platform that uses Retrieval-Augmented Generation (RAG) to help developers understand, analyze, and interact with their codebases. It provides semantic code search, intelligent code explanations, and conversational AI assistance for any repository.

## Live Demo & Documentation

- **🌐 Hosted Frontend Website:** [codesense-frontend.vercel.app](https://codesense-frontend.vercel.app/)
- **💻 Frontend GitHub Repository:** [github.com/ariwalapratham/codesense-frontend](https://github.com/ariwalapratham/codesense-frontend)
- **📄 API Swagger Docs:** [codesense-o8zr.onrender.com/docs](https://codesense-o8zr.onrender.com/docs)

## Features

- **🔍 Semantic Code Search**: Find code by meaning, not just keywords using vector embeddings
- **💬 AI-Powered Chat**: Ask questions about your codebase and get contextual answers with citations
- **📚 Repository Ingestion**: Automatically parse, chunk, and index entire codebases
- **🌳 Code Structure Analysis**: Understand code structure, dependencies, and relationships
- **📊 Intelligent Parsing**: Extract functions, classes, imports, and more using tree-sitter
- **🚀 Streaming Responses**: Real-time streaming chat responses using Server-Sent Events (SSE)
- **🔄 Rate Limit Management**: Built-in rate limiting and retry logic for API calls
- **📝 Session Management**: Persistent chat sessions with conversation history and summaries

## Tech Stack

### Backend
- **FastAPI**: Modern, fast web framework for building APIs
- **MongoDB**: NoSQL database for storing repositories, chunks, and chat sessions
- **FAISS**: Vector similarity search for semantic code retrieval
- **Google Gemini**: LLM for embeddings and chat responses
- **Tree-sitter**: Code parsing and structure analysis
- **GitPython**: Git repository operations

### Frontend
- [Frontend GitHub Repository](#) - *Add your frontend repository link here*
- [Deployed Application](#) - *Add your deployed frontend application link here*

## Prerequisites

- Python 3.11+
- MongoDB (local or remote instance)
- Google Gemini API key
- Git (for repository cloning)

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd codesense
```

### 2. Create Virtual Environment

```bash
python3 -m venv env
source env/bin/activate  # On Windows: env\Scripts\activate
```

### 3. Install Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 4. Environment Configuration

Create a `.env` file in the `backend` directory:

```env
# Gemini Configuration
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
GEMINI_LLM_MODEL=gemini-2.5-flash
GEMINI_LLM_FALLBACK_MODEL=gemini-2.5-flash-lite

# MongoDB Configuration
MONGODB_URL=mongodb://localhost:27017
DB_NAME=codesense

# Vector Store Configuration
VECTOR_STORE_PATH=../vector_stores

# Repository Processing Configuration
TEMP_REPOS_PATH=../temp_repos

# Optional: GitHub/GitLab Token (for private repos)
GITHUB_TOKEN=your_github_token_here
GITLAB_TOKEN=your_gitlab_token_here
```

### 5. Start MongoDB

Make sure MongoDB is running on your system:

```bash
# Using Docker
docker run -d -p 27017:27017 --name mongodb mongo:latest

# Or using system service
sudo systemctl start mongod
```

### 6. Run the Application

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`

## API Documentation

Once the server is running, you can access:
- **Interactive API Docs**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Health Check**: http://localhost:8000/health

## Docker Deployment

### Build the Docker Image

```bash
docker build -t codesense-backend .
```

### Run with Docker

```bash
docker run -d \
  --name codesense-backend \
  -p 8000:8000 \
  -v $(pwd)/vector_stores:/app/vector_stores \
  -v $(pwd)/temp_repos:/app/temp_repos \
  --env-file backend/.env \
  codesense-backend
```

### Docker Compose (Recommended)

Create a `docker-compose.yml`:

```yaml
version: '3.8'

services:
  mongodb:
    image: mongo:latest
    ports:
      - "27017:27017"
    volumes:
      - mongodb_data:/data/db

  backend:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./vector_stores:/app/vector_stores
      - ./temp_repos:/app/temp_repos
    env_file:
      - backend/.env
    depends_on:
      - mongodb

volumes:
  mongodb_data:
```

Run with:

```bash
docker-compose up -d
```

## Project Structure

```
codesense/
├── backend/
│   ├── api/
│   │   └── routes/
│   │       ├── chat.py          # Chat API endpoints
│   │       └── repository.py    # Repository management endpoints
│   ├── config/
│   │   └── settings.py          # Configuration management
│   ├── database/
│   │   └── mongodb.py           # MongoDB connection
│   ├── models/
│   │   └── schemas/             # Pydantic models
│   ├── services/
│   │   ├── chat_service.py      # Chat orchestration
│   │   ├── embedding_service.py # Embedding generation
│   │   ├── ingestion_service.py # Repository ingestion
│   │   ├── llm_service.py       # LLM interactions
│   │   ├── mongodb_service.py   # MongoDB operations
│   │   ├── parser_service.py   # Code parsing
│   │   ├── retrieval_service.py # RAG retrieval
│   │   └── vector_store_service.py # FAISS operations
│   ├── utils/
│   │   ├── prompts.py           # LLM prompts
│   │   └── sse.py               # SSE utilities
│   ├── main.py                  # FastAPI application
│   └── requirements.txt         # Python dependencies
├── vector_stores/               # FAISS vector stores
├── temp_repos/                  # Temporary repository clones
└── README.md
```

## API Endpoints

### Repository Management

- `GET /repository/` - List all repositories
- `POST /repository/ingest` - Ingest a new repository
- `GET /repository/{repo_id}` - Get repository details
- `GET /repository/{repo_id}/stats` - Get repository statistics
- `GET /repository/ingest/status/{request_id}` - Get ingestion status
- `POST /repository/ingest/retry` - Retry failed ingestion
- `DELETE /repository/{repo_id}` - Delete repository

### Chat

- `POST /repository/{repo_id}/chat-sessions` - Create chat session
- `GET /repository/{repo_id}/chat-sessions` - List chat sessions
- `GET /repository/{repo_id}/chat-sessions/{session_id}` - Get chat session
- `DELETE /repository/{repo_id}/chat-sessions/{session_id}` - Delete chat session
- `POST /repository/{repo_id}/chat-sessions/{session_id}/messages` - Send message
- `POST /repository/{repo_id}/chat-sessions/{session_id}/messages/stream` - Stream chat response (SSE)
- `GET /repository/{repo_id}/chat-sessions/{session_id}/messages` - Get message history

## Usage Examples

### Ingest a Repository

```bash
curl -X POST "http://localhost:8000/repository/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "repository_url": "https://github.com/owner/repo.git",
    "owner": "owner",
    "repo_name": "repo",
    "branch": "main"
  }'
```

### Create a Chat Session

```bash
curl -X POST "http://localhost:8000/repository/{repo_id}/chat-sessions" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Send a Chat Message (Streaming)

```bash
curl -X POST "http://localhost:8000/repository/{repo_id}/chat-sessions/{session_id}/messages/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "How does authentication work?",
    "stream": true
  }'
```

## Development

### Running Tests

```bash
# Add test commands here when tests are added
```

### Code Style

The project follows PEP 8 style guidelines. Consider using:
- `black` for code formatting
- `pylint` for linting
- `mypy` for type checking

## License

See [LICENSE](LICENSE) file for details.

## Support

For issues, questions, or contributions, please open an issue on GitHub.

---

Built with ❤️ using FastAPI, MongoDB, FAISS, and Google Gemini
