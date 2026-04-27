# Rune Server

A modular FastAPI server for the Rune Protocol that provides:

- **LLM Gateway**: Multi-provider LLM proxy (Gemini, OpenAI, Anthropic)
- **Authentication**: JWT and WebAuthn passkey support
- **Event Sync Hub**: Bidirectional event synchronization with clients
- **Chain Proxy**: ERC-8004 agent registration and queries
- **User Profiles**: Profile management and updates

## Architecture

The server is organized into focused modules:

```
rune_server/
├── __init__.py           # Package exports
├── config.py             # Environment-based configuration
├── auth.py               # JWT + WebAuthn authentication router
├── llm_gateway.py        # Multi-provider LLM proxy router
├── sync_hub.py           # Event synchronization router
├── chain_proxy.py        # Blockchain operations router
├── user_profile.py       # User profile management router
├── middleware.py         # Rate limiting and shared utilities
├── database.py           # SQLite connection and schema management
└── main.py               # FastAPI app assembly and entry point
```

Each module is self-contained and can be understood independently.

## Installation

```bash
pip install -e ".[llm]"
```

The `llm` extra includes dependencies for all LLM providers:
```bash
pip install -e ".[llm]"
```

For development:
```bash
pip install -e ".[dev]"
```

## Configuration

All server settings are loaded from environment variables with sensible defaults:

```bash
# Server basics
SERVER_HOST=0.0.0.0
SERVER_PORT=8001
SERVER_SECRET=your-secret-key
ENVIRONMENT=development

# LLM Configuration
DEFAULT_LLM_PROVIDER=anthropic  # gemini, openai, anthropic
DEFAULT_LLM_MODEL=claude-3-sonnet-20240229
GEMINI_API_KEY=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...

# Authentication
JWT_EXPIRATION_HOURS=24
WEBAUTHN_RP_ID=localhost
WEBAUTHN_RP_NAME=Rune Protocol
WEBAUTHN_ORIGIN=http://localhost:3000

# Blockchain
CHAIN_RPC_URL=...
SERVER_PRIVATE_KEY=...

# CORS
CORS_ALLOW_ORIGINS=http://localhost:3000,http://localhost:5173

# Database
DATABASE_URL=sqlite:///./rune_server.db

# Rate Limiting
RATE_LIMIT_LLM_REQUESTS_PER_MINUTE=60
RATE_LIMIT_OTHER_REQUESTS_PER_MINUTE=120
```

## Running the Server

### Development

```bash
python -m rune_server.main --reload
```

### Using CLI Entry Point

```bash
rune-server --port 8001 --reload
```

### Production

```bash
rune-server --port 8001 --host 0.0.0.0
```

The server will start at `http://localhost:8001` with a health check endpoint at `/health`.

## API Endpoints

### Health Check
- `GET /health` - Server health and version

### Authentication
- `POST /api/v1/auth/register` - Register with display name
- `POST /api/v1/auth/login` - Login with user ID
- `POST /api/v1/auth/passkey/register/start` - Start WebAuthn registration
- `POST /api/v1/auth/passkey/register/finish` - Finish WebAuthn registration
- `POST /api/v1/auth/passkey/login/start` - Start WebAuthn login
- `POST /api/v1/auth/passkey/login/finish` - Finish WebAuthn login

### LLM Gateway
- `POST /api/v1/llm/chat` - Chat with configured LLM provider

### Event Sync
- `POST /api/v1/sync/push` - Push events from client
- `GET /api/v1/sync/pull` - Pull events after a sync point

### User Profile
- `GET /api/v1/user/profile` - Get current user's profile
- `PUT /api/v1/user/profile` - Update current user's profile

### Chain
- `POST /api/v1/chain/register-agent` - Register ERC-8004 agent
- `GET /api/v1/chain/agent/{agent_id}` - Get agent info

## Database

The server uses SQLite with three tables:

- **users**: User accounts with JWT secrets and WebAuthn credentials
- **sync_events**: Event log for bidirectional synchronization
- **rate_limits**: Rate limit tracking per user/endpoint

Tables are created automatically on startup.

## Rate Limiting

Rate limiting is applied per user per endpoint using a 1-minute sliding window:

- LLM endpoints: 60 requests/minute (configurable)
- Other endpoints: 120 requests/minute (configurable)

Rate limit entries are stored in the `rate_limits` table.

## Authentication Flow

### JWT Login
1. Client calls `POST /api/v1/auth/register` or `/api/v1/auth/login`
2. Server returns JWT token signed with user-specific secret
3. Client includes token in `Authorization: Bearer <token>` header

### WebAuthn Passkey
1. Client calls `POST /api/v1/auth/passkey/register/start` to get challenge
2. Client uses challenge to register credential with WebAuthn API
3. Client calls `POST /api/v1/auth/passkey/register/finish` with credential
4. For login: similar flow but `/passkey/login/start` and `/passkey/login/finish`

## Event Synchronization

Clients can synchronize state with the server:

1. **Push**: Client sends new events to `POST /api/v1/sync/push`
   - Server assigns auto-incrementing `sync_id` to each event
   - Server timestamps events with `server_received_at`

2. **Pull**: Client queries `GET /api/v1/sync/pull?after={sync_id}`
   - Server returns all events after the specified sync_id
   - Client updates local state with received events

This allows robust offline-first synchronization.

## LLM Gateway

The gateway proxies requests to configured providers:

```json
{
  "messages": [
    {"role": "user", "content": "Hello, world!"}
  ],
  "system_prompt": "You are a helpful assistant",
  "model": "claude-3-sonnet-20240229",
  "temperature": 0.7,
  "max_tokens": 1024
}
```

The gateway:
- Routes to the configured `DEFAULT_LLM_PROVIDER`
- Applies rate limiting per user
- Handles provider-specific API differences
- Returns responses in a unified format

## Development

### Running Tests

```bash
pytest tests/
```

### Code Quality

Format:
```bash
black rune_server/
```

Lint:
```bash
ruff check rune_server/
```

Type check:
```bash
mypy rune_server/
```

## Module Overview

### config.py
Centralized configuration from environment variables. Single source of truth for all settings.

### auth.py
JWT token creation/verification and WebAuthn challenge generation. Authentication routes and the `get_current_user` dependency.

### database.py
SQLite connection management and schema initialization. Uses context managers for clean resource handling.

### llm_gateway.py
Routes to Gemini, OpenAI, and Anthropic APIs. Handles provider-specific request/response format differences.

### sync_hub.py
Event push/pull endpoints for client synchronization. Assigns sync_ids and tracks server timestamps.

### chain_proxy.py
ERC-8004 agent registration and queries. In production would interact with blockchain.

### user_profile.py
User profile read/update endpoints. Manages display_name and timestamps.

### middleware.py
Rate limiting using sliding 1-minute windows. Shared utility functions.

### main.py
FastAPI app creation, router registration, middleware setup, and entry point.

## License

MIT
