# `auth/` — Passkey + JWT

What's in here:

| File | Purpose |
|---|---|
| `routes.py` | `/api/v1/auth/*` HTTP endpoints + `get_current_user` dependency + JWT create/verify |
| `passkey_page.py` | The HTML/JS payload served at `/passkey` for browser-side WebAuthn ceremonies (registration + login) |
| `__init__.py` | Re-exports the public surface (`router`, `get_current_user`, `create_jwt`, `verify_jwt`, `passkey_page`) |

What the new dev needs to know:

- **Passkey registration** runs through `/passkey` (the HTML page) which talks to `/api/v1/auth/register/start` and `/api/v1/auth/register/finish`. After success the server hands back a JWT; subsequent requests carry it as `Authorization: Bearer ...`.
- **Login** is the symmetric flow: `/api/v1/auth/login/start` + `/api/v1/auth/login/finish`.
- `get_current_user` is the FastAPI dependency every authenticated route uses. It verifies the JWT and returns `user_id` (UUID).
- The `users` table schema lives in `rune_server.database`; this module reads/writes through it.

What's NOT in here:

- WebAuthn ceremony validation logic — handled by the `webauthn` Python package, called from `routes.py`.
- The `chain_agent_id` registration that runs on first chat — that's `chain/bootstrap.py`.
