#!/usr/bin/env python3
"""
generate_cards.py

Runs LOCALLY only. Reads your notes, asks Gemini to turn them into
atomic Q&A flashcards (as strict JSON), and pushes them straight into
Firestore using a service account (bypasses security rules, since this
is you, not the public web app).

Usage:
    python generate_cards.py notes/some_note.md --type yazilim --topic "React"
    python generate_cards.py notes/ --type kpss --topic "Anayasa Hukuku"

--type is required and must be "yazilim" or "kpss".
--topic is required and is free text (the konu shown in the web app's step 2).

Setup (one-time):
    pip install -r requirements.txt
    cp .env.example .env   # then put your GEMINI_API_KEY in it
    # download serviceAccountKey.json from Firebase console
    # (Project settings > Service accounts > Generate new private key)
    # place it next to this script. It's already in .gitignore.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

# .env dosyasından GEMINI_API_KEY / GEMINI_MODEL oku (varsa)
load_dotenv(Path(__file__).parent / ".env")

# ---- config -----------------------------------------------------------

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
SERVICE_ACCOUNT_PATH = Path(__file__).parent / "serviceAccountKey.json"
CHUNK_CHARS = 3000  # keep chunks small so nothing gets skipped/truncated

# The two card types the web app filters on (step 1). Kept lowercase/ascii
# so they're safe as stored values; the UI maps them to display labels.
CARD_TYPES = ("yazilim", "kpss")

# ---- prompt -------------------------------------------------------------
# The whole "quality" of the app lives in this prompt. Two failure modes
# we're explicitly guarding against: (1) skipping facts, (2) vague/lazy
# questions that don't actually test recall.

SYSTEM_PROMPT = """You are an expert flashcard writer creating study material \
from a student's notes.

Rules, follow all of them:
1. Go through the text and extract EVERY distinct fact, definition, \
relationship, number, name, formula, cause/effect, or example. Do not \
skip anything, even details that seem minor. If in doubt, make a card \
for it rather than omitting it.
2. Each card must be ATOMIC: one fact or one relationship per card. If a \
sentence contains three facts, make three cards, not one crowded card.
3. Never write a question that can be answered with "yes"/"no" or that \
just repeats the sentence structure. Ask "what/why/how/when/which" in a \
way that requires recalling the actual content.
4. The "front" (question) must make sense on its own, without the \
student having the source text in front of them. Do not write vague \
questions like "What does the text say about this?".
5. The "back" (answer) must be a short, precise, self-contained answer \
-- a phrase or one or two sentences. Do not pad it with explanation \
that wasn't in the source.
6. Write the question and answer in the SAME LANGUAGE as the source \
text.
7. Output ONLY a JSON array, no prose before or after, matching this \
exact shape:
[{"front": "...", "back": "...", "tags": ["optional","short","tags"]}]
8. If the text is too short or has nothing worth testing, return [].
"""


def chunk_text(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split on blank lines/paragraphs, keeping chunks under max_chars,
    so we never silently truncate content the model never saw."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks, current = [], ""
    for p in paragraphs:
        if len(current) + len(p) + 2 > max_chars and current:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks or [text]


def call_gemini(model, chunk: str, retries: int = 3) -> list[dict]:
    prompt = f"{SYSTEM_PROMPT}\n\n---\nSOURCE TEXT:\n{chunk}\n---"
    for attempt in range(1, retries + 1):
        try:
            response = model.generate_content(prompt)
            raw = response.text.strip()
            # models sometimes wrap JSON in ```json fences despite instructions
            if raw.startswith("```"):
                raw = raw.strip("`")
                raw = raw[4:] if raw.lower().startswith("json") else raw
            cards = json.loads(raw)
            if not isinstance(cards, list):
                raise ValueError("Expected a JSON array")
            cleaned = []
            for c in cards:
                if isinstance(c, dict) and c.get("front") and c.get("back"):
                    cleaned.append({
                        "front": str(c["front"]).strip(),
                        "back": str(c["back"]).strip(),
                        "tags": c.get("tags", []) or [],
                    })
            return cleaned
        except Exception as e:
            print(f"    [retry {attempt}/{retries}] parse/generation error: {e}",
                  file=sys.stderr)
            time.sleep(2 * attempt)
    print("    giving up on this chunk after retries", file=sys.stderr)
    return []


def read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collect_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(
        p for p in target.rglob("*")
        if p.suffix.lower() in {".md", ".txt"} and p.is_file()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="note file or folder")
    parser.add_argument("--type", dest="card_type", required=True,
                        choices=CARD_TYPES,
                        help="card type / alan: 'yazilim' or 'kpss'")
    parser.add_argument("--topic", required=True,
                        help='konu, free text, e.g. "React" or "Anayasa Hukuku"')
    args = parser.parse_args()

    topic = args.topic.strip()
    if not topic:
        sys.exit("--topic cannot be empty.")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("Set GEMINI_API_KEY first: export GEMINI_API_KEY=...")
    if not SERVICE_ACCOUNT_PATH.exists():
        sys.exit(f"Missing {SERVICE_ACCOUNT_PATH}. Download it from the "
                  "Firebase console (Project settings > Service accounts).")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        generation_config={"response_mime_type": "application/json"},
    )

    cred = credentials.Certificate(str(SERVICE_ACCOUNT_PATH))
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    files = collect_files(Path(args.path))
    if not files:
        sys.exit("No .md/.txt files found at that path.")

    print(f"Type: {args.card_type}  |  Topic: {topic}\n")

    total = 0
    for f in files:
        print(f"Processing {f} ...")
        text = read_source(f)
        for i, chunk in enumerate(chunk_text(text), start=1):
            print(f"  chunk {i}...")
            cards = call_gemini(model, chunk)
            for card in cards:
                doc = {
                    "front": card["front"],
                    "back": card["back"],
                    "type": args.card_type,
                    "topic": topic,
                    "tags": sorted(set(card["tags"] + [args.card_type, topic])),
                    "source": f.name,
                    "createdAt": datetime.now(timezone.utc),
                    # spaced repetition (SM-2) state, fresh card:
                    "repetitions": 0,
                    "easeFactor": 2.5,
                    "interval": 0,
                    "nextReview": datetime.now(timezone.utc),
                    "lastReviewed": None,
                }
                db.collection("cards").add(doc)
                total += 1
            print(f"    +{len(cards)} cards")

    print(f"\nDone. {total} cards pushed to Firestore.")


if __name__ == "__main__":
    main()
