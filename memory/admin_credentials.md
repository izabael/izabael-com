---
name: Marlowe admin credentials
description: Admin login for izabael.com — username, email, default passphrase
type: reference
---

Marlowe's admin account on izabael.com:

- **Username:** marlowe
- **Email:** izabael@gmail.com
- **Password:** purple-wings-netzach-1984
- **Role:** admin
- **Created:** 2026-04-07

Password is PBKDF2-SHA256 hashed in the SQLite DB. Change via Python:
```python
from database import _hash_password, _db
new_hash = _hash_password("new-passphrase-here")
await _db.execute("UPDATE users SET password_hash = ? WHERE username = 'marlowe'", (new_hash,))
await _db.commit()
```
