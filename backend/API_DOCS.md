# Repository Ingestion API Documentation

Simple API reference for UI integration.

## 1. Create Ingestion Request

**Endpoint:** `POST /repository/ingest`

**Description:** Start ingesting a new repository or retry a failed one.

**Request Body:**
```json
{
  "repo_url": "https://github.com/owner/repo",
  "branch": "main",
  "cleanup": true
}
```

**Response:** `202 Accepted`
```json
{
  "request_id": "6948e2aa9b551331c82c817d",
  "encoded_id": "eyJicmFuY2giOiJtYWluIiwib3duZXIiOiJvd25lciIsInJlcG9fbmFtZSI6InJlcG8ifQ",
  "repo_id": "6948e303825861a796c7ba28",
  "owner": "owner",
  "repo_name": "repo",
  "branch": "main",
  "commit_hash": "6ec0e10",
  "status": "pending",
  "message": "Ingestion request created and queued"
}
```

**Important Fields:**
- `encoded_id` - **Use this for status queries!** Base64-encoded identifier that can be regenerated from `owner/repo_name/branch`
- `request_id` - MongoDB ObjectId (also works for status, but `encoded_id` is preferred)
- `owner`, `repo_name`, `branch` - Use these to regenerate `encoded_id` after page reload

**Error Responses:**
- `409 Conflict` - Repository already being processed (includes existing `request_id` and `encoded_id`)
- `429 Too Many Requests` - Rate limit exceeded (max 3 attempts per 2 hours)

---

## 2. Get Ingestion Status

**Endpoint:** `GET /repository/ingest/status/{identifier}`

**Description:** Check the status of an ingestion request. Use `encoded_id` (recommended) or `request_id` (MongoDB ObjectId).

**Path Parameter:**
- `identifier` - Either:
  - `encoded_id` (base64 string) - **Recommended** - Can be regenerated from repo info
  - `request_id` (MongoDB ObjectId) - Also works for backward compatibility

**Response:** `200 OK`
```json
{
  "request_id": "6948e2aa9b551331c82c817d",
  "repo_id": "6948e303825861a796c7ba28",
  "owner": "owner",
  "repo_name": "repo",
  "branch": "main",
  "commit_hash": "6ec0e10",
  "status": "processing",
  "error": null,
  "retry_count": 0,
  "retry_after": null,
  "message": "Processing repository...",
  "created_at": "2024-01-01T10:00:00Z",
  "started_at": "2024-01-01T10:00:05Z",
  "completed_at": null
}
```

**Status Values:**
- `pending` - Request queued, waiting to start
- `processing` - Currently ingesting repository
- `completed` - Successfully completed
- `failed` - Ingestion failed (show retry button)

**Error Responses:**
- `404 Not Found` - Request not found

---

## 3. Retry Failed Ingestion

**Endpoint:** `POST /repository/ingest/{request_id}/retry`

**Description:** Retry a failed ingestion request. Only works if status is `failed`.

**Path Parameter:**
- `request_id` - MongoDB ObjectId from the ingestion request

**Response:** `202 Accepted`
```json
{
  "request_id": "6948e2aa9b551331c82c817d",
  "encoded_id": "eyJicmFuY2giOiJtYWluIiwib3duZXIiOiJvd25lciIsInJlcG9fbmFtZSI6InJlcG8ifQ",
  "repo_id": "6948e303825861a796c7ba28",
  "owner": "owner",
  "repo_name": "repo",
  "branch": "main",
  "commit_hash": "6ec0e10",
  "status": "pending",
  "message": "Ingestion retry initiated"
}
```

**Error Responses:**
- `400 Bad Request` - Cannot retry (status is not `failed`)
- `404 Not Found` - Request ID not found
- `429 Too Many Requests` - Retry rate limit exceeded (max 3 retries per 2 hours)

---

## 4. List Repositories

**Endpoint:** `GET /repository/`

**Description:** Get a paginated list of repositories. **Includes both completed repositories and in-progress ingestion requests.**

**Query Parameters:**
- `skip` (optional, default: 0) - Number of repositories to skip
- `limit` (optional, default: 10, max: 100) - Maximum number of repositories to return
- `search` (optional) - Search term to filter by repo_name, owner, or repository_url (case-insensitive)

**Response:** `200 OK`
```json
{
  "repositories": [
    {
      "id": "6948e303825861a796c7ba28",
      "repo_name": "completed-repo",
      "repository_url": "https://github.com/owner/completed-repo.git",
      "status": "completed",
      "files_processed": 245,
      "created_at": "2024-01-01T10:00:00Z",
      "updated_at": "2024-01-01T10:15:00Z"
    },
    {
      "id": "eyJicmFuY2giOiJtYWluIiwib3duZXIiOiJvd25lciIsInJlcG9fbmFtZSI6InByb2Nlc3NpbmctcmVwbyJ9",
      "repo_name": "processing-repo",
      "repository_url": "https://github.com/owner/processing-repo.git",
      "status": "processing",
      "files_processed": 0,
      "created_at": "2024-01-01T11:00:00Z",
      "updated_at": "2024-01-01T11:00:05Z"
    }
  ],
  "total": 2,
  "skip": 0,
  "limit": 10
}
```

**Important Notes:**
- **In-progress repositories are included** - Repositories with status `pending` or `processing` appear in the list immediately
- **ID field varies**:
  - **Completed repos**: `id` is MongoDB ObjectId (e.g., `"6948e303825861a796c7ba28"`)
  - **In-progress repos**: `id` is `encoded_id` (base64 string) - use this for status queries
- **Status values**: `pending`, `processing`, `completed`, `failed`
- **Files processed**: In-progress repos show `files_processed: 0` until ingestion completes
- **Deduplication**: If a repo exists in both collections, the completed repository entry takes precedence
- **Sorting**: Results are sorted by `updated_at` descending (most recent first)

**Usage Example:**
```javascript
// List repositories with search
const response = await fetch('/repository/?skip=0&limit=20&search=react');
const data = await response.json();

// Handle each repository
data.repositories.forEach(repo => {
  // Check if it's in-progress (encoded_id) or completed (ObjectId)
  const isInProgress = repo.status === 'pending' || repo.status === 'processing';
  
  if (isInProgress) {
    // Use id directly as encoded_id for status queries
    pollStatus(repo.id);
  } else {
    // Completed repo - can navigate to chat or details
    showRepositoryCard(repo);
  }
});
```

---

## UI Integration Flow

### Step 1: User clicks "Add Repository"
```javascript
// Call ingest endpoint
const response = await fetch('/repository/ingest', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    repo_url: 'https://github.com/owner/repo',
    branch: 'main',
    cleanup: true
  })
});

const data = await response.json();
// Store encoded_id for status queries
const statusId = data.encoded_id;
// Also store owner/repo_name/branch for regeneration
const repoInfo = {
  owner: data.owner,
  repo_name: data.repo_name,
  branch: data.branch
};

// Navigate to repositories page - the new repo will appear in the list immediately
// (it will show as "pending" or "processing" status)
```

### Step 2: Poll for status updates
```javascript
// Poll every 2-5 seconds
const pollStatus = async (identifier) => {
  const response = await fetch(`/repository/ingest/status/${identifier}`);
  const status = await response.json();
  
  // Update UI based on status
  switch (status.status) {
    case 'pending':
      showBadge('Queued');
      break;
    case 'processing':
      showBadge('Processing', spinner: true);
      break;
    case 'completed':
      showBadge('Completed');
      enableChatButton();
      break;
    case 'failed':
      showBadge('Failed');
      enableRetryButton();
      break;
  }
  
  // Continue polling if not completed/failed
  if (status.status === 'pending' || status.status === 'processing') {
    setTimeout(() => pollStatus(identifier), 3000);
  }
};

// Start polling
pollStatus(statusId);
```

### Step 3: Load repositories page (shows in-progress repos)
```javascript
// Fetch repositories list (includes in-progress repos)
const reposResponse = await fetch('/repository/?skip=0&limit=50');
const reposData = await reposResponse.json();

reposData.repositories.forEach(repo => {
  // Check if repository is in-progress
  const isInProgress = repo.status === 'pending' || repo.status === 'processing';
  
  if (isInProgress) {
    // repo.id is already encoded_id for in-progress repos
    // Start polling status
    pollStatus(repo.id);
  } else {
    // Completed repository - show normal card
    displayRepositoryCard(repo);
  }
});
```

### Step 4: Handle page reload
```javascript
// After page reload, regenerate encoded_id from repo info
function encodeRepoIdentifier(owner, repoName, branch) {
  const data = {
    owner: owner,
    repo_name: repoName,
    branch: branch || 'main'
  };
  // Sort keys for consistency
  const sorted = Object.keys(data).sort().reduce((acc, key) => {
    acc[key] = data[key];
    return acc;
  }, {});
  
  const jsonStr = JSON.stringify(sorted);
  const encoded = btoa(jsonStr);
  // Remove padding
  return encoded.replace(/=+$/, '');
}

// When loading repositories page, regenerate encoded_id
// OR: Just fetch the list - in-progress repos already have encoded_id as id
const reposResponse = await fetch('/repository/');
const reposData = await reposResponse.json();
// In-progress repos will have encoded_id as id, so you can poll directly
```

### Step 5: User clicks "Retry" (if failed)
```javascript
// Use request_id from status response
const retryResponse = await fetch(
  `/repository/ingest/${status.request_id}/retry`,
  { method: 'POST' }
);

const retryData = await retryResponse.json();
// Resume polling with encoded_id
pollStatus(retryData.encoded_id);
```

---

## Generating encoded_id Client-Side

Since `encoded_id` is deterministic, you can generate it client-side:

```javascript
function generateEncodedId(owner, repoName, branch = 'main') {
  const data = {
    branch: branch,
    owner: owner,
    repo_name: repoName
  };
  
  // Sort keys for consistency (matches backend)
  const sortedKeys = Object.keys(data).sort();
  const sorted = {};
  sortedKeys.forEach(key => {
    sorted[key] = data[key];
  });
  
  const jsonStr = JSON.stringify(sorted);
  const encoded = btoa(jsonStr);
  return encoded.replace(/=+$/, ''); // Remove padding
}

// Usage
const encodedId = generateEncodedId('owner', 'repo', 'main');
// Use this encodedId for status queries
```

---

## Notes

- **Use `encoded_id` for status queries** - It's deterministic and can be regenerated
- **Store `owner/repo_name/branch`** - These are stable identifiers that survive page reloads
- **In-progress repos appear in list** - Repositories being ingested show up immediately in `GET /repository/` with status `pending` or `processing`
- **ID field handling** - In-progress repos use `encoded_id` as `id`, completed repos use MongoDB ObjectId
- **Duplicate prevention** - Duplicate ingestion attempts are automatically blocked (returns 409)
- **Rate limits** - Max 3 ingestion attempts per repository per 2 hours
- **Processing time** - Typically takes 2-5 minutes depending on repository size
- **Status polling** - Poll every 2-5 seconds, stop when status is `completed` or `failed`
- **Backward compatibility** - `request_id` (MongoDB ObjectId) still works, but `encoded_id` is preferred

---

## Example: Complete Flow

```javascript
// 1. User adds repository
const ingestResponse = await fetch('/repository/ingest', {
  method: 'POST',
  body: JSON.stringify({ repo_url: 'https://github.com/user/repo', branch: 'main' })
});
const { encoded_id, owner, repo_name, branch } = await ingestResponse.json();

// 2. Navigate to repositories page - repo appears immediately
// Fetch repositories list
const reposResponse = await fetch('/repository/');
const reposData = await reposResponse.json();

// 3. Find the in-progress repo and poll its status
const inProgressRepo = reposData.repositories.find(
  r => r.repo_name === repo_name && (r.status === 'pending' || r.status === 'processing')
);

if (inProgressRepo) {
  // repo.id is already encoded_id for in-progress repos
  pollStatus(inProgressRepo.id);
}

// 4. On page reload - just fetch the list again
// In-progress repos will have encoded_id as id, so you can poll directly
const reloadResponse = await fetch('/repository/');
const reloadData = await reloadResponse.json();
reloadData.repositories
  .filter(r => r.status === 'pending' || r.status === 'processing')
  .forEach(repo => pollStatus(repo.id)); // repo.id is encoded_id
```
