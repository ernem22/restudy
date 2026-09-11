#!/usr/bin/env python3
"""
import_yazilim.py — HAZIR "Soru: / Cevap:" notlarını Gemini KULLANMADAN
doğrudan Firestore'a yazar.

import_kpss.py'den farkı: Gemini'ye hiçbir şey gönderilmez. Kaynak dosya
zaten temiz soru-cevap blokları içerdiği için basit bir parser yeterli;
bu hem ücretsiz hem deterministiktir (kayıp kart yok, [] dönmesi yok,
ücret yok).

Beklenen format (notes/yazilim/*.md):

    Soru: useState ne işe yarar?
    Cevap: Function component'lerde state tanımlayan Hook'tur.

    Soru: ...
    Cevap: ...

Kurallar: her blok "Soru:" satırıyla başlar, "Cevap:" satırı (tek veya
çok satır) ile devam eder. İlk "Soru:" satırından önceki her şey (başlık,
kullanım notu) yok sayılır.

Kullanım:
    python import_yazilim.py notes/yazilim/react-kolay.md --topic "React Kolay" --dry-run
    python import_yazilim.py notes/yazilim/react-kolay.md --topic "React Kolay"
    python import_yazilim.py notes/yazilim/ --topic "React"   # klasördeki tüm .md'ler

Bayraklar:
    --topic    zorunlu, konu adı (web arayüzü 2. adımda gösterir)
    --type     kart tipi: yazilim (varsayılan) | kpss
    --dry-run  Firestore'a YAZMA, sadece parse edip göster
"""

import argparse
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import FieldFilter

SERVICE_ACCOUNT_PATH = Path(__file__).parent / "serviceAccountKey.json"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_qa(text: str) -> tuple[list[dict], int]:
    """Metindeki 'Soru:/Cevap:' bloklarını çıkarır.

    Döner: (kartlar, cevapsız_atlanan_blok_sayısı)
    """
    cards: list[dict] = []
    skipped = 0
    cur_q: list[str] | None = None
    cur_a: list[str] | None = None

    def flush():
        nonlocal skipped
        if cur_q is None:
            return
        q = " ".join(cur_q).strip()
        a = " ".join(cur_a).strip() if cur_a else ""
        if q and a:
            cards.append({"front": q, "back": a})
        else:
            skipped += 1

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("Soru:"):
            flush()
            cur_q = [line[len("Soru:"):].strip()]
            cur_a = None
        elif line.startswith("Cevap:") and cur_q is not None:
            cur_a = [line[len("Cevap:"):].strip()]
        elif cur_q is not None and line:
            # Cevap'ın devam satırı (çok satırlı cevap desteği)
            if cur_a is not None:
                cur_a.append(line)
            else:
                cur_q.append(line)
    flush()
    return cards, skipped


def collect_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(
        p for p in target.rglob("*")
        if p.suffix.lower() in {".md", ".txt"} and p.is_file()
    )


def existing_keys(db, card_type: str) -> set[tuple[str, str]]:
    """(topic, normalize edilmiş front) — dedup amaçlı."""
    out = set()
    for d in db.collection("cards").where(
        filter=FieldFilter("type", "==", card_type)
    ).stream():
        x = d.to_dict()
        out.add((x.get("topic", ""), norm(x.get("front", ""))))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="not dosyası veya klasörü")
    parser.add_argument("--topic", required=True, help='konu, ör. "React Kolay"')
    parser.add_argument("--type", dest="card_type", default="yazilim",
                        choices=("yazilim", "kpss"), help="kart tipi")
    parser.add_argument("--dry-run", action="store_true",
                        help="Firestore'a yazma, sadece göster")
    args = parser.parse_args()

    topic = args.topic.strip()
    if not topic:
        sys.exit("--topic boş olamaz.")

    files = collect_files(Path(args.path))
    if not files:
        sys.exit("No .md/.txt files found at that path.")

    # --- parse (DB'ye dokunmadan) ---
    per_file: list[tuple[Path, list[dict], int]] = []
    for f in files:
        cards, skipped = parse_qa(f.read_text(encoding="utf-8"))
        per_file.append((f, cards, skipped))

    total_parsed = sum(len(c) for _, c, _ in per_file)
    total_skipped = sum(s for _, _, s in per_file)
    if not total_parsed:
        sys.exit("Hiç soru bloğu bulunamadı. Dosyada 'Soru:' satırı yok.")

    if args.dry_run:
        print(f"--- DRY RUN: {total_parsed} kart parse edildi (yazılmadı)"
              + (f", {total_skipped} cevapsız blok atlandı" if total_skipped else "")
              + " ---\n")
        n = 0
        for f, cards, _ in per_file:
            print(f"== {f.name}: {len(cards)} soru")
            for c in cards[:3]:
                n += 1
                print(f"[{n}] {c['front']}\n    -> {c['back'][:120]}")
            if len(cards) > 3:
                print(f"    ... (+{len(cards) - 3} soru daha)")
        return

    # --- yazma ---
    if not SERVICE_ACCOUNT_PATH.exists():
        sys.exit(f"Eksik: {SERVICE_ACCOUNT_PATH}")
    cred = credentials.Certificate(str(SERVICE_ACCOUNT_PATH))
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    already = existing_keys(db, args.card_type)
    now = datetime.now(timezone.utc)
    written, dupes = Counter(), 0
    for f, cards, _ in per_file:
        for c in cards:
            key = (topic, norm(c["front"]))
            if key in already:
                dupes += 1
                continue
            already.add(key)
            db.collection("cards").add({
                "front": c["front"],
                "back": c["back"],
                "type": args.card_type,
                "topic": topic,
                "tags": [args.card_type, topic],
                "source": f.name,
                "createdAt": now,
                "repetitions": 0,
                "easeFactor": 2.5,
                "interval": 0,
                "nextReview": now,
                "lastReviewed": None,
            })
            written[topic] += 1

    total = sum(written.values())
    print(f"\nBitti. {total} kart yazıldı"
          + (f", {dupes} kopya atlandı" if dupes else "") + ".")
    for t, n in sorted(written.items()):
        print(f"  {args.card_type} / {t}: +{n}")


if __name__ == "__main__":
    main()
