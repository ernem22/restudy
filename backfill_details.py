#!/usr/bin/env python3
"""
backfill_details.py — DETAIL backfill scripti (Gemini YOK, içerik elde yazılır).

Akış:
    1. Kartları Firestore'dan çek (front/back görmek için):
         python backfill_details.py --export --type yazilim --topic "React Kolay"
       -> notes/details/React Kolay.json ({"doc_id": {"front":..., "back":..., "detail":""}})
    2. JSON'daki "detail" alanlarını elde doldur.
    3. Önce --dry-run ile kontrol, sonra gerçek yazım:
         python backfill_details.py --import notes/details/React Kolay.json --dry-run
         python backfill_details.py --import notes/details/React Kolay.json

Sadece `detail` alanını update eder, başka alana dokunmaz (batch write, 400'lük gruplar).
"""

import argparse
import json
import sys
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

SERVICE_ACCOUNT_PATH = Path(__file__).parent / "serviceAccountKey.json"
DETAILS_DIR = Path(__file__).parent / "notes" / "details"


def get_db():
    if not SERVICE_ACCOUNT_PATH.exists():
        sys.exit(f"Eksik: {SERVICE_ACCOUNT_PATH}")
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(credentials.Certificate(str(SERVICE_ACCOUNT_PATH)))
    return firestore.client()


def cmd_export(db, card_type, topic):
    from google.cloud.firestore_v1 import FieldFilter
    docs = (
        db.collection("cards")
        .where(filter=FieldFilter("type", "==", card_type))
        .where(filter=FieldFilter("topic", "==", topic))
        .stream()
    )
    out = {}
    for d in docs:
        x = d.to_dict()
        out[d.id] = {
            "front": x.get("front", ""),
            "back": x.get("back", ""),
            "detail": x.get("detail", ""),
        }
    DETAILS_DIR.mkdir(parents=True, exist_ok=True)
    path = DETAILS_DIR / f"{topic}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    filled = sum(1 for v in out.values() if v.get("detail"))
    print(f"{len(out)} kart -> {path} ({filled} tanesinde detay var)")


def cmd_import(db, path, dry_run):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = [(k, (v.get("detail") or "").strip()) for k, v in data.items() if isinstance(v, dict)]
    items = [(k, d) for k, d in items if d]
    if not items:
        sys.exit("Yazılacak detay yok (tüm detail alanları boş).")
    print(f"--- {'DRY RUN' if dry_run else 'YAZIM'}: {len(items)} karta detail basılacak ---")
    for k, d in items[:3]:
        print(f"[{k[:6]}...] {d[:120]}")
    if len(items) > 3:
        print(f"    ... (+{len(items) - 3} kart daha)")
    if dry_run:
        return
    batch, n, done = db.batch(), 0, 0
    for k, d in items:
        batch.update(db.collection("cards").document(k), {"detail": d})
        n += 1
        if n >= 400:
            batch.commit()
            done += n
            n = 0
            batch = db.batch()
    if n:
        batch.commit()
        done += n
    print(f"Bitti. {done} kart güncellendi.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--export", action="store_true", help="kartları JSON'a dök")
    p.add_argument("--import", dest="imp", help="dolu JSON'u Firestore'a bas")
    p.add_argument("--type", dest="card_type", default="yazilim")
    p.add_argument("--topic", default="")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    db = get_db()
    if args.export:
        if not args.topic:
            sys.exit("--export ile --topic zorunlu.")
        cmd_export(db, args.card_type, args.topic)
    elif args.imp:
        cmd_import(db, args.imp, args.dry_run)
    else:
        sys.exit("--export veya --import seç.")


if __name__ == "__main__":
    main()
