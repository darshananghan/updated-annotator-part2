import csv
import json
import random
from pathlib import Path

INPUT_CSV = "questions.csv"
OUT_DIR = Path("batches")
QUESTIONS_PER_BATCH = 20

OUT_DIR.mkdir(exist_ok=True)

# Read questions
questions = []
with open(INPUT_CSV, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        qid = row.get("question_id") or row.get("id") or None
        qtext = row.get("question_text") or row.get("text") or row.get("question") or None
        if qtext is None:
            raise ValueError("CSV must have a column named question_text (or text/question).")
        questions.append({"question_id": qid, "question_text": qtext})

if len(questions) < QUESTIONS_PER_BATCH:
    raise SystemExit("Not enough questions to form one batch.")

# Shuffle and split into batches
random.shuffle(questions)
batches = [questions[i:i+QUESTIONS_PER_BATCH] for i in range(0, len(questions), QUESTIONS_PER_BATCH)]

# If last batch < QUESTIONS_PER_BATCH you can either
# 1) discard it, 2) merge with previous, or 3) allow a smaller final batch.
# Here we will keep it (Label Studio supports varying numbers).
for i, batch in enumerate(batches, start=1):
    tasks = []
    for q in batch:
        # Label Studio import format: a list of task objects with "data"
        tasks.append({
            "data": {
                "question_text": q["question_text"]
            },
            "meta": {
                "question_id": q["question_id"]
            }
        })
    out_file = OUT_DIR / f"batch_{i}.json"
    with open(out_file, "w", encoding="utf-8") as fo:
        json.dump(tasks, fo, ensure_ascii=False, indent=2)
    print(f"Wrote {out_file} ({len(tasks)} tasks)")

print("Done. Import the JSON files in the batches/ folder to Label Studio.")
