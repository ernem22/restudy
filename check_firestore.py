#!/usr/bin/env python3
"""
check_firestore.py — bir seferlik bağlantı testi.

serviceAccountKey.json ile Firestore'a bağlanır, geçici bir "yazilim" test
kartı YAZAR → geri OKUR → SİLER. Gemini'ye ihtiyaç yok.

Amaç: admin kimlik bilgisi + Firestore erişimi + yaz/oku/sil zincirinin
çalıştığını doğrulamak. (Tarayıcı tarafındaki firestore.rules'u doğrulamaz —
onun için web uygulamasına giriş yapman gerekir.)

    pip install -r requirements.txt
    python check_firestore.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import FieldFilter

SERVICE_ACCOUNT_PATH = Path(__file__).parent / "serviceAccountKey.json"
TEST_TOPIC = "__baglanti_testi__"


def main():
    if not SERVICE_ACCOUNT_PATH.exists():
        sys.exit(f"Missing {SERVICE_ACCOUNT_PATH}")

    cred = credentials.Certificate(str(SERVICE_ACCOUNT_PATH))
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print(f"[OK] Bağlanıldı: proje '{cred.project_id}'")

    now = datetime.now(timezone.utc)
    doc = {
        "front": "Bağlantı testi — bu kart otomatik silinecek.",
        "back": "OK",
        "type": "yazilim",
        "topic": TEST_TOPIC,
        "tags": ["yazilim", TEST_TOPIC],
        "source": "check_firestore.py",
        "createdAt": now,
        "repetitions": 0,
        "easeFactor": 2.5,
        "interval": 0,
        "nextReview": now,
        "lastReviewed": None,
    }

    ref = db.collection("cards").add(doc)[1]
    print(f"[OK] Yazıldı: cards/{ref.id}")

    snap = ref.get()
    if not snap.exists:
        sys.exit("[HATA] Yazılan kart geri okunamadı.")
    data = snap.to_dict()
    print(f"[OK] Okundu: type={data['type']!r}  topic={data['topic']!r}")

    ref.delete()
    print(f"[OK] Silindi: cards/{ref.id}")

    # temizlik doğrulaması: aynı topic'te artık kart kalmamalı
    leftovers = list(
        db.collection("cards")
        .where(filter=FieldFilter("topic", "==", TEST_TOPIC))
        .stream()
    )
    if leftovers:
        print(f"[UYARI] Uyarı: {len(leftovers)} test kartı hâlâ duruyor, elle sil.")
    else:
        print("[OK] Temiz.")

    print("\nHepsi çalışıyor. Artık generate_cards.py ile gerçek kart üretebilirsin.")


if __name__ == "__main__":
    main()
