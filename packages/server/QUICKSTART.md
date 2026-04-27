# Quick Start Guide

## Installation

```bash
cd packages/server
pip install -e ".[llm]"
```

## Configuration

Create `.env` file in the server directory:

```bash
# Minimal setup for local development
ENVIRONMENT=development
SERVER_PORT=8001
SERVER_SECRET=dev-secret

# Required: Pick one LLM provider
ANTHROPIC_API_KEY=sk-ant-...
# OR
OPENAI_API_KEY=sk-...
# OR
GEMINI_API_KEY=...

# Optional: WebAuthn (adjust for your setup)
WEBAUTHN_RP_ID=localhost
WEBAUTHN_ORIGIN=http://localhost:3000

# Optional: Chain (for agent registration)
# CHAIN_RPC_URL=https://bsc-dataseed.binance.org
# SERVER_PRIVATE_KEY=0x...
```

## Run Server

```bash
# Development with auto-reload
python -m rune_server.main --reload

# Or using CLI
rune-server --reload

# Production
rune-server --host 0.0.0.0 --port 8001
```

Server starts at `http://localhost:8001`

## Test Health

```bash
curl http://localhost:8001/health
```

Response:
```json
{
  "status": "healthy",
  "timestamp": "2024-04-26T12:00:00Z",
  "version": "0.1.0"
}
```

## Register & Login

### Simple Registration

```bash
curl -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Alice"}'
```

Response:
```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "jwt_token": "eyJhbGc...",
  "created_at": "2024-04-26T12:00:00Z"
}
```

Save the `jwt_token` for authenticated requests.

### Login with User ID

```bash
curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"user_id": "550e8400-e29b-41d4-a716-446655440000"}'
```

## Use Protected Endpoints

All authenticated endpoints require: `Authorization: Bearer <jwt_token>`

### Get Profile

```bash
curl -H "Authorization: Bearer eyJhbGc..." \
  http://localhost:8001/api/v1/user/profile
```

### Update Profile

```bash
curl -X PUT http://localhost:8001/api/v1/user/profile \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{"display_name": "Alice Updated"}'
```

## Chat with LLM

```bash
curl -X POST http://localhost:8001/api/v1/llm/chat \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "Hello, world!"}
    ],
    "system_prompt": "You are a helpful assistant",
    "temperature": 0.7,
    "max_tokens": 1024
  }'
```

Response:
```json
{
  "role": "assistant",
  "content": "Hello! How can I help you today?",
  "model": "claude-3-sonnet-20240229",
  "stop_reason": "stop"
}
```

## Push Events (Sync)

```bash
curl -X POST http://localhost:8001/api/v1/sync/push \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{
    "events": [
      {
        "event_type": "user_action",
        "content": "{\"action\": \"click\", \"target\": \"button\"}",
        "session_id": "session-123",
        "metadata": {"timestamp": 1234567890}
      }
    ]
  }'
```

Response:
```json
{
  "assigned_sync_ids": [1],
  "server_time": "2024-04-26T12:00:00Z"
}
```

## Pull Events (Sync)

```bash
curl -H "Authorization: Bearer eyJhbGc..." \
  "http://localhost:8001/api/v1/sync/pull?after=0"
```

Response:
```json
{
  "events": [
    {
      "sync_id": 1,
      "event_type": "user_action",
      "content": "{\"action\": \"click\", \"target\": \"button\"}",
      "session_id": "session-123",
      "metadata": {"timestamp": 1234567890},
      "server_received_at": "2024-04-26T12:00:00Z"
    }
  ],
  "latest_sync_id": 1
}
```

## Register Agent (Chain)

```bash
curl -X POST http://localhost:8001/api/v1/chain/register-agent \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "My Agent",
    "metadata": {"version": "1.0"}
  }'
```

Response (development mode - no RPC configured):
```json
{
  "agent_id": "123e4567-e89b-12d3-a456-426614174000",
  "tx_hash": null,
  "status": "pending"
}
```

## WebAuthn Passkey Registration

### Start Registration

```bash
curl -X POST http://localhost:8001/api/v1/auth/passkey/register/start \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Bob",
    "user_agent": "Mozilla/5.0..."
  }'
```

Response:
```json
{
  "challenge": "...",
  "user_id": "550e8400-e29b-41d4-a716-446655440001",
  "rp_id": "localhost",
  "rp_name": "Rune Protocol"
}
```

### Finish Registration (requires WebAuthn credential from client)

```bash
curl -X POST http://localhost:8001/api/v1/auth/passkey/register/finish \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "550e8400-e29b-41d4-a716-446655440001",
    "display_name": "Bob",
    "credential": {
      "id": "...",
      "type": "public-key",
      "response": {...}
    }
  }'
```

## Database

SQLite database is created automatically at `./rune_server.db`

View tables:
```bash
sqlite3 rune_server.db ".tables"
```

Query users:
```bash
sqlite3 rune_server.db "SELECT id, display_name, created_at FROM users;"
```

Query events:
```bash
sqlite3 rune_server.db "SELECT sync_id, event_type, user_id FROM sync_events;"
```

## Rate Limiting

Rate limits are enforced per user per endpoint:
- LLM endpoints: 60 requests/minute
- Other endpoints: 120 requests/minute

When limit is exceeded:
```json
{
  "error": "Rate limit exceeded",
  "status_code": 429,
  "timestamp": "2024-04-26T12:00:00Z"
}
```

## Troubleshooting

### Server won't start
Check:
- Port 8001 is available: `lsof -i :8001`
- Dependencies installed: `pip install -e ".[llm]"`
- Python 3.10+: `python --version`

### LLM endpoints fail
Check:
- API key is set: `echo $ANTHROPIC_API_KEY`
- API provider is configured in .env
- Default provider matches available keys

### WebAuthn fails
Check:
- `WEBAUTHN_RP_ID` matches your domain
- `WEBAUTHN_ORIGIN` matches your frontend
- Browser supports WebAuthn (Chrome, Firefox, Safari)

### Database locked
SQLite is single-writer. If getting "database is locked":
- Kill other server processes
- Delete `.db-wal` and `.db-shm` files
- Restart server

## Next Steps

- Read [ARCHITECTURE.md](ARCHITECTURE.md) for design patterns
- Check [README.md](README.md) for detailed API docs
- Review module source code for implementation details
- Write tests in `tests/` directory

## Module Structure

```
rune_server/
├── config.py          # Configuration from env vars
├── database.py        # SQLite connection & schema
├── auth.py            # JWT + WebAuthn routes
├── llm_gateway.py     # LLM proxy routes
├── sync_hub.py        # Event sync routes
├── chain_proxy.py     # Chain operation routes
├── user_profile.py    # Profile management routes
├── middleware.py      # Rate limiting utilities
├── main.py            # FastAPI app factory
└── __init__.py        # Package exports
```

Each module is self-contained and can be understood independently.
