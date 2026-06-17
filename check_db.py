"""
Quick diagnostic: shows collection counts and sample documents from MongoDB.

Reads connection details from .streamlit/secrets.toml (same as the app),
or from MONGO_URI / MONGO_DB environment variables.
"""

import os
import sys
import pymongo


def _get_connection():
    secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(secrets_path, "rb") as f:
            secrets = tomllib.load(f)
        return secrets["mongo"]["uri"], secrets["mongo"]["db_name"]

    uri = os.environ.get("MONGO_URI", "")
    db_name = os.environ.get("MONGO_DB", "implicit_bias_study")
    if not uri:
        sys.exit(
            "Error: MongoDB URI not found.\n"
            "Create .streamlit/secrets.toml or set the MONGO_URI environment variable."
        )
    return uri, db_name


uri, db_name = _get_connection()
client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
db = client[db_name]

# ── study_sessions ─────────────────────────────────────────────────────────
print("\n=== study_sessions ===")
total = db.study_sessions.count_documents({})
print(f"Total documents: {total}")
if total > 0:
    for doc in db.study_sessions.find({}, {"_id": 0}).sort("submitted_at", -1).limit(3):
        print(doc)

# ── responses ──────────────────────────────────────────────────────────────
print("\n=== responses ===")
total_r = db.responses.count_documents({})
print(f"Total documents: {total_r}")
if total_r > 0:
    for doc in db.responses.find({}, {"_id": 0}).limit(3):
        print(doc)

# ── questions ──────────────────────────────────────────────────────────────
print("\n=== questions ===")
print(f"Total documents: {db.questions.count_documents({})}")

client.close()
