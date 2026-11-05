"""Generated cards (build/<topic>/cards.jsonl) -> Firestore `cards` collection.

DRY-RUN BY DEFAULT: nothing is written unless --commit is given.

    python push_cards.py --topic react                   # local dry-run, no network
    python push_cards.py --topic react --check-remote    # dry-run + READ existing cards (no writes)
    python push_cards.py --topic react --commit          # really write (new cards only)

Document ID is deterministic (type|topic|normalized question), so re-running never duplicates and
never overwrites an existing card (its study progress is kept). Rows whose lesson mentions code
terms that are not in the source (`flags` "ders:") are pushed WITHOUT the lesson; rows whose cards
are flagged ("kart:") are skipped entirely. Both are reported.
"""
import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
SERVICE_ACCOUNT_PATH = ROOT / "serviceAccountKey.json"
TOPIC_NAMES = {"react": "React", "javascript": "JavaScript", "javascript-info": "JavaScript (javascript.info)",
               "typescript": "TypeScript", "nodejs": "Node.js", "react-native": "React Native"}


def norm(text):
    return " ".join(text.split()).casefold()


def read_jsonl(path):
    if not path.exists():
        sys.exit(f"yok: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def doc_id(card_type, topic, front):
    return hashlib.sha1(f"{card_type}|{topic}|{norm(front)}".encode("utf-8")).hexdigest()[:20]


def build_docs(topic_key, topic_name):
    base = BUILD / topic_key
    sections = {s["sectionId"]: s for s in read_jsonl(base / "sections.jsonl")}
    rows = read_jsonl(base / "cards.jsonl")
    now = datetime.now(timezone.utc)
    docs, seen = {}, set()
    stats = Counter()
    for row in rows:
        section = sections[row["sectionId"]]
        flags = row.get("flags", [])
        if any(f.startswith("kart:") for f in flags):
            stats["rows_skipped_flagged_cards"] += 1
            continue
        lesson = row["lesson"]
        if any(f.startswith("ders:") for f in flags):
            lesson = ""  # suspicious lesson: keep the cards, drop the lesson
            stats["lessons_dropped_flagged"] += 1
        group = section.get("group") or "general"
        for card in row["cards"]:
            front = card["question"].strip()
            did = doc_id(group, topic_name, front)
            if did in seen:
                stats["duplicate_questions_skipped"] += 1
                continue
            seen.add(did)
            docs[did] = {
                "front": front,
                "back": card["answer"].strip(),
                "detail": lesson,
                "type": group,
                "topic": topic_name,
                "tags": [group, topic_name],
                "source": section["sourceUrl"],
                "sourceTitle": section["headingPath"],
                "evidence": card["evidence"],
                "sectionId": section["sectionId"],
                "createdAt": now,
                "repetitions": 0,
                "easeFactor": 2.5,
                "interval": 0,
                "nextReview": now,
                "lastReviewed": None,
            }
            stats["cards"] += 1
            stats["cards_with_lesson"] += 1 if lesson else 0
    stats["rows"] = len(rows)
    return docs, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topic", required=True, help="build/<topic> klasörü, ör. react")
    parser.add_argument("--topic-name", help="uygulamada görünecek konu adı (varsayılan: tablo veya başlık biçimi)")
    parser.add_argument("--check-remote", action="store_true", help="Firestore'dan mevcut kartları OKU (yazma yok)")
    parser.add_argument("--commit", action="store_true", help="GERÇEKTEN yaz (yalnızca yeni kartlar)")
    parser.add_argument("--fill-lessons", action="store_true",
                        help="mevcut kartlardan `detail`'i BOŞ olanlara dersi yaz (yalnızca detail alanı; çalışma ilerlemesine dokunmaz). "
                             "--commit olmadan yalnızca sayar.")
    args = parser.parse_args()

    topic_name = args.topic_name or TOPIC_NAMES.get(args.topic, args.topic.title())
    docs, stats = build_docs(args.topic, topic_name)
    groups = Counter(d["type"] for d in docs.values())
    print(f"konu: {topic_name} | alan(type): {dict(groups)}")
    print(f"hazırlanan kart: {stats['cards']} (dersli: {stats['cards_with_lesson']}) | satır: {stats['rows']}")
    print(f"atlanan/çıkarılan: kartı işaretli satır {stats['rows_skipped_flagged_cards']}, "
          f"dersi işaretli (ders çıkarıldı) {stats['lessons_dropped_flagged']}, tekrar soru {stats['duplicate_questions_skipped']}")

    if not (args.check_remote or args.commit or args.fill_lessons):
        print("\nDRY-RUN (yerel). Firestore'a bağlanılmadı, hiçbir şey yazılmadı.")
        return

    if args.fill_lessons:
        import firebase_admin
        from firebase_admin import credentials, firestore
        firebase_admin.initialize_app(credentials.Certificate(str(SERVICE_ACCOUNT_PATH)))
        db = firestore.client()
        print(f"hedef proje: {db.project}  koleksiyon: cards")
        ids = list(docs)
        todo = []
        for start in range(0, len(ids), 300):
            for snap in db.get_all([db.collection("cards").document(i) for i in ids[start:start + 300]]):
                if snap.exists and not (snap.to_dict().get("detail") or "").strip() and docs[snap.id]["detail"]:
                    todo.append(snap.id)
        print(f"detail'i boş olan mevcut kart: {len(todo)} (yalnızca `detail` alanı yazılacak)")
        if not args.commit:
            print("\nDRY-RUN (uzak okuma). Hiçbir şey yazılmadı. Yazmak için --commit ekle.")
            return
        done = 0
        for start in range(0, len(todo), 400):
            batch = db.batch()
            for i in todo[start:start + 400]:
                batch.update(db.collection("cards").document(i), {"detail": docs[i]["detail"]})
            batch.commit()
            done += len(todo[start:start + 400])
            print(f"güncellendi: {done}/{len(todo)}", flush=True)
        print(f"\nBitti. {done} kartın yalnızca `detail` alanı yazıldı.")
        return

    import firebase_admin
    from firebase_admin import credentials, firestore
    firebase_admin.initialize_app(credentials.Certificate(str(SERVICE_ACCOUNT_PATH)))
    db = firestore.client()
    print(f"hedef proje: {db.project}  koleksiyon: cards")
    ids = list(docs)
    existing = set()
    for start in range(0, len(ids), 300):
        for snap in db.get_all([db.collection("cards").document(i) for i in ids[start:start + 300]]):
            if snap.exists:
                existing.add(snap.id)
    new_ids = [i for i in ids if i not in existing]
    print(f"Firestore'da zaten var: {len(existing)} | yeni yazılacak: {len(new_ids)}")

    if not args.commit:
        print("\nDRY-RUN (uzak okuma). Hiçbir şey yazılmadı. Yazmak için --commit ekle.")
        return

    written = 0
    for start in range(0, len(new_ids), 400):  # batch limiti 500
        batch = db.batch()
        for i in new_ids[start:start + 400]:
            batch.create(db.collection("cards").document(i), docs[i])
        batch.commit()
        written += len(new_ids[start:start + 400])
        print(f"yazıldı: {written}/{len(new_ids)}", flush=True)
    print(f"\nBitti. {written} yeni kart yazıldı, {len(existing)} mevcut kart dokunulmadı.")


if __name__ == "__main__":
    main()
