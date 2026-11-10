import os

# app/config.py refuses to load with the insecure default secret key or a
# short auth token — give the test process real-looking values before any
# app module is imported. Nothing here connects to a database.
os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "0" * 48)
os.environ.setdefault("AUTH_TOKEN", "test-auth-token-" + "0" * 48)
