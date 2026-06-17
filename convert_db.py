"""
Export all study data from MongoDB to Excel.

Before running, set your MongoDB connection in .streamlit/secrets.toml
or set the MONGO_URI / MONGO_DB environment variables:

  Windows : set MONGO_URI=mongodb+srv://...
  Mac/Linux: export MONGO_URI=mongodb+srv://...
"""

import os
import sys
from datetime import datetime

import pandas as pd
import pymongo

# ── Connection config ─────────────────────────────────────────────────────────
# Prefer secrets.toml when running alongside the Streamlit app locally.
def _get_connection():
    # Try reading .streamlit/secrets.toml
    secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                tomllib = None

        if tomllib:
            with open(secrets_path, "rb") as f:
                secrets = tomllib.load(f)
            return secrets["mongo"]["uri"], secrets["mongo"]["db_name"]

    # Fall back to environment variables
    uri = os.environ.get("MONGO_URI", "")
    db_name = os.environ.get("MONGO_DB", "implicit_bias_study")
    if not uri:
        sys.exit(
            "Error: MongoDB URI not found.\n"
            "Either create .streamlit/secrets.toml or set the MONGO_URI environment variable."
        )
    return uri, db_name


def export_to_excel(output_file: str):
    uri, db_name = _get_connection()
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]

    print(f"Connected to MongoDB database: {db_name}")

    # Fetch all collections
    sessions   = pd.DataFrame(list(db.study_sessions.find({}, {"_id": 0})))
    responses  = pd.DataFrame(list(db.responses.find({}, {"_id": 0})))
    questions  = pd.DataFrame(list(db.questions.find({}, {"_id": 0})))

    if responses.empty:
        print("WARNING: No responses found in the database.")
        client.close()
        return

    # Build the merged responses sheet
    merged = responses.merge(
        sessions, on="session_id", how="left", suffixes=("", "_session")
    )
    if not questions.empty:
        merged = merged.merge(
            questions[["question_id", "question_text", "true_label"]],
            on="question_id",
            how="left",
        )

    # Reorder columns for readability
    front_cols = [
        "prolific_id", "study_id", "prolific_session_id", "session_id",
        "question_id", "question_text", "true_label",
        "part_1_textarea", "part_2_option",
        "confidence_level", "age_group", "gender",
        "religion_familiarity", "region", "caste_category", "english_comfort",
        "timestamp", "submitted_at",
    ]
    ordered_cols = [c for c in front_cols if c in merged.columns]
    extra_cols   = [c for c in merged.columns if c not in ordered_cols]
    merged = merged[ordered_cols + extra_cols]

    print(f"Exporting to {output_file} ...")
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        merged.to_excel(writer, sheet_name="Responses", index=False)
        if not sessions.empty:
            sessions.to_excel(writer, sheet_name="Sessions", index=False)

    unique_pids = sessions["prolific_id"].nunique() if not sessions.empty else 0
    print(f"Done!")
    print(f"  Unique Prolific IDs : {unique_pids}")
    print(f"  Sessions            : {len(sessions)}")
    print(f"  Response rows       : {len(responses)}")
    print(f"  Saved to            : {output_file}")
    client.close()


if __name__ == "__main__":
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_to_excel(f"responses_{timestamp}.xlsx")
