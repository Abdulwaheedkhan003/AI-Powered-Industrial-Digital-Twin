"""Authentication, roles and login rate limiting.

Three roles:
  viewer   : anyone who can reach the server; read-only.
  operator : run simulations, acknowledge alerts, manage work orders, run diagnoses.
  admin    : everything an operator can do, plus pairing the ESP32, sending
             device commands and changing settings.

Permissions are enforced here on the server. Hiding a button in the browser is
never the only protection.

Tokens are signed with HMAC-SHA256 using the secret from the local .env file.
This is suitable for a local demonstration. A deployment needs TLS, per-user
accounts with hashed passwords, and an identity provider.
"""
import base64
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque

ROLE_LEVEL = {"viewer": 0, "operator": 1, "admin": 2}
MAX_FAILED_LOGINS = 5
LOCK_WINDOW_S = 60


class AuthError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class Security:
    def __init__(self, settings):
        self.settings = settings
        self.failed = defaultdict(deque)

    def _sign(self, body):
        return hmac.new(self.settings.secret.encode(), body, hashlib.sha256).hexdigest()

    def login(self, username, password, client="local"):
        now = time.time()
        attempts = self.failed[client]
        while attempts and now - attempts[0] > LOCK_WINDOW_S:
            attempts.popleft()
        if len(attempts) >= MAX_FAILED_LOGINS:
            raise AuthError(429, "Too many failed sign-in attempts. Wait a minute and try again.")
        expected = {"admin": self.settings.admin_password, "operator": self.settings.operator_password}.get(username)
        if not self.settings.secret or not expected or not hmac.compare_digest(expected.encode(), password.encode()):
            attempts.append(now)
            raise AuthError(401, "Incorrect username or password.")
        attempts.clear()
        body = base64.urlsafe_b64encode(json.dumps(
            {"sub": username, "role": username, "exp": now + self.settings.token_ttl_s}).encode())
        return {"token": f"{body.decode()}.{self._sign(body)}", "user": username, "role": username,
                "expires": now + self.settings.token_ttl_s}

    def identify(self, authorization):
        """Return {"user", "role"} for a request. No or bad token -> viewer."""
        viewer = {"user": "viewer", "role": "viewer"}
        if not authorization or not authorization.startswith("Bearer ") or not self.settings.secret:
            return viewer
        try:
            body, signature = authorization[7:].split(".", 1)
            if not hmac.compare_digest(signature, self._sign(body.encode())):
                return viewer
            claims = json.loads(base64.urlsafe_b64decode(body.encode()))
            if claims["exp"] < time.time() or claims["role"] not in ROLE_LEVEL:
                return viewer
            return {"user": claims["sub"], "role": claims["role"]}
        except (ValueError, KeyError, json.JSONDecodeError):
            return viewer

    @staticmethod
    def require(identity, role):
        if ROLE_LEVEL[identity["role"]] < ROLE_LEVEL[role]:
            if identity["role"] == "viewer":
                raise AuthError(401, f"Sign in as {role} to do this.")
            raise AuthError(403, f"This action needs the {role} role.")
