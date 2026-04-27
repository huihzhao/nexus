# Rune Server Architecture

## Overview

The monolithic `nexus/server/api_server.py` has been refactored into a modular FastAPI application with clean separation of concerns. Each module is self-contained and has a single responsibility.

## Module Map

### Core Infrastructure

| Module | Purpose | Lines | Key Exports |
|--------|---------|-------|------------|
| `config.py` | Environment configuration | 102 | `ServerConfig`, `get_config()` |
| `database.py` | SQLite management | 86 | `get_db_connection()`, `init_db()` |
| `middleware.py` | Rate limiting | 74 | `check_rate_limit()` |

### Feature Routers

| Module | Purpose | Lines | Endpoints |
|--------|---------|-------|-----------|
| `auth.py` | JWT + WebAuthn | 526 | `/api/v1/auth/*` (8 routes) |
| `llm_gateway.py` | LLM proxy | 300 | `/api/v1/llm/chat` (1 route + 3 providers) |
| `sync_hub.py` | Event sync | 234 | `/api/v1/sync/push`, `/api/v1/sync/pull` |
| `chain_proxy.py` | Chain ops | 151 | `/api/v1/chain/*` (2 routes) |
| `user_profile.py` | Profile mgmt | 166 | `/api/v1/user/profile` (2 routes) |

### Application Assembly

| Module | Purpose | Lines |
|--------|---------|-------|
| `main.py` | FastAPI app factory, lifecycle, error handling | 271 |
| `__init__.py` | Package exports | 29 |

## Design Patterns

### 1. Router Modules
Each feature lives in its own router module that:
- Defines request/response Pydantic models
- Implements business logic
- Exports a FastAPI `router` for inclusion in main app
- Is fully independent and testable

```python
# Example from auth.py
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

@router.post("/register", response_model=UserRegisterResponse)
async def register_user(request: UserRegisterRequest) -> UserRegisterResponse:
    # Implementation
    pass
```

### 2. Context Manager for Database
SQLite connections use Python's context manager protocol for safety:

```python
# From database.py
@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    db_path = config.DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# Usage
with get_db_connection() as conn:
    cursor = conn.cursor()
    # do work
```

### 3. Dependency Injection
FastAPI `Depends()` is used for:
- Authentication: `get_current_user` dependency in protected routes
- Rate limiting: Manual checks in route handlers

```python
@router.post("/chat")
async def llm_chat(
    request: LLMChatRequest,
    current_user: str = Depends(get_current_user),
) -> LLMChatResponse:
    # current_user is automatically extracted and validated
    pass
```

### 4. Middleware Stack
- CORS middleware added in `main.py` app factory
- Exception handlers registered in `main.py` for consistent error responses
- Rate limiting implemented as inline middleware in route handlers

### 5. Configuration as Singleton
```python
from rune_server.config import get_config

config = get_config()
# All config reads come from environment at import time
```

## Data Flow

### Authentication
```
User Registration/Login
  ↓
auth.register_user() or auth.login_user()
  ↓
Create user in SQLite (users table)
  ↓
create_jwt_token(user_id, jwt_secret)
  ↓
Return JWT to client
```

### Protected Endpoint
```
Client Request with Authorization header
  ↓
FastAPI extracts header, calls get_current_user dependency
  ↓
verify_jwt_token(token, user_id) checks DB
  ↓
Route handler executes with user_id
```

### Event Sync
```
Client: POST /api/v1/sync/push
  ↓
Server: Store events in sync_events table, assign sync_id
  ↓
Return [assigned_sync_ids]
  ↓
Client: GET /api/v1/sync/pull?after={last_sync_id}
  ↓
Server: Query sync_events where sync_id > after
  ↓
Return events with metadata
```

### LLM Gateway
```
POST /api/v1/llm/chat
  ↓
get_current_user validates JWT
  ↓
check_rate_limit(user_id, endpoint, limit)
  ↓
call_llm(messages, system_prompt, model, ...)
  ↓
call_anthropic() / call_openai() / call_gemini()
  ↓
Return LLMChatResponse
```

## Database Schema

### users
```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    passkey_credential TEXT,          -- JSON-encoded WebAuthn credential
    jwt_secret TEXT NOT NULL,         -- Per-user JWT signing secret
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
)
```

### sync_events
```sql
CREATE TABLE sync_events (
    sync_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    content TEXT NOT NULL,            -- JSON-encoded event data
    session_id TEXT,
    metadata TEXT,                    -- JSON-encoded metadata
    client_created_at TIMESTAMP,
    server_received_at TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
```

### rate_limits
```sql
CREATE TABLE rate_limits (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    request_count INTEGER NOT NULL,
    window_start TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
```

## Error Handling

All HTTP errors are formatted consistently:

```json
{
    "error": "Rate limit exceeded",
    "status_code": 429,
    "timestamp": "2024-04-26T12:00:00Z"
}
```

Exception handlers in `main.py`:
- `http_exception_handler`: Catches FastAPI HTTPException
- `generic_exception_handler`: Catches unexpected exceptions

## Rate Limiting

Implemented in `middleware.py` using sliding 1-minute windows:

1. Query `rate_limits` table for current window
2. If request_count >= limit, return False
3. Otherwise increment counter
4. On new window, insert new entry

Configuration per endpoint:
- LLM endpoints: `RATE_LIMIT_LLM_REQUESTS_PER_MINUTE` (default 60)
- Other endpoints: `RATE_LIMIT_OTHER_REQUESTS_PER_MINUTE` (default 120)

## Startup Sequence

```
main.py: run_server() or __main__
  ↓
create_app() via FastAPI(lifespan=lifespan)
  ↓
lifespan.__aenter__()
  ├── logger.info("Starting...")
  ├── config.validate()
  ├── init_db()
  └── yield (app runs)
  ↓
lifespan.__aexit__()
  └── logger.info("Shutting down...")
```

## Module Dependencies

```
main.py
  ├── config (env-based)
  ├── database (SQLite)
  ├── auth (router)
  ├── llm_gateway (router)
  ├── sync_hub (router)
  ├── chain_proxy (router)
  └── user_profile (router)

auth.py
  ├── config
  ├── database
  └── pydantic (models)

llm_gateway.py
  ├── config
  ├── auth (get_current_user)
  ├── middleware (check_rate_limit)
  └── pydantic (models)

sync_hub.py
  ├── auth
  ├── config
  ├── database
  ├── middleware
  └── pydantic

chain_proxy.py
  ├── auth
  ├── config
  └── pydantic

user_profile.py
  ├── auth
  ├── database
  └── pydantic

middleware.py
  └── database

database.py
  └── config
```

## Code Metrics

| Metric | Value |
|--------|-------|
| Total Lines | 1,939 |
| Modules | 8 (core) |
| Routers | 5 (auth, llm, sync, chain, profile) |
| Endpoints | 14+ |
| Request Models | 20+ |
| Response Models | 20+ |

## Testing Strategy

Each module can be tested independently:

```python
# test_auth.py
from rune_server import auth
from fastapi.testclient import TestClient

async def test_register_user():
    # Test auth.register_user directly
    pass

# test_llm_gateway.py
from rune_server import llm_gateway

async def test_llm_chat():
    # Mock get_current_user, test llm_gateway.llm_chat
    pass
```

## Deployment Notes

### Environment Variables
Required for production:
- `SERVER_SECRET`: Unique secret key
- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` or `GEMINI_API_KEY`
- `WEBAUTHN_RP_ID`: Domain name for WebAuthn

Optional for blockchain features:
- `CHAIN_RPC_URL`: Web3 RPC endpoint
- `SERVER_PRIVATE_KEY`: Signing key for transactions

### Database
SQLite path is configurable via `DATABASE_URL`. For production:
- Use a persistent mount
- Consider WAL mode for concurrent access
- Regular backups of `rune_server.db`

### Scaling
Current implementation is single-process SQLite. For production scale:
- Consider PostgreSQL for `rate_limits` table
- Use Redis for rate limiting
- Implement distributed rate limits across multiple server instances
