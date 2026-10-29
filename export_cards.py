#!/usr/bin/env python3
"""
export_cards.py — Firestore'daki tüm kartları dışa aktar + kalite denetimi.

serviceAccountKey.json ile `cards` koleksiyonunu okur ve:

  cards_export.json   — ham döküm (her kartın tamamı)
  review_report.md    — gözden geçirme raporu: tip/konu bazlı gruplu
                        soru-cevap-detail listesi + denetim bulguları

Gemini'ye ihtiyaç yok. Salt okuma: hiçbir doküman yazılmaz.

    pip install -r requirements.txt
    python export_cards.py            # ikisini de üretir
    python export_cards.py --kpss     # sadece kpss
    python export_cards.py --yazilim  # sadece yazilim
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

SERVICE_ACCOUNT_PATH = Path(__file__).parent / "serviceAccountKey.json"
EXPORT_PATH = Path(__file__).parent / "cards_export.json"
REPORT_PATH = Path(__file__).parent / "review_report.md"


def md_to_text(s: str) -> str:
    """Rapor için markdown'i hafifçe düzleştir."""
    s = s or ""
    s = re.sub(r"```[a-zA-Z]*\n(.*?)```", r"\1", s, flags=re.S)
    s = re.sub(r"^#{1,4}\s+", "", s, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    return " ".join(s.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yazilim", action="store_true")
    ap.add_argument("--kpss", action="store_true")
    args = ap.parse_args()
    types = [t for t, want in (("yazilim", args.yazilim), ("kpss", args.kpss)) if want]
    if not types:
        types = ["yazilim", "kpss"]

    if not SERVICE_ACCOUNT_PATH.exists():
        sys.exit(
            f"{SERVICE_ACCOUNT_PATH} yok.\n"
            "Console > Project settings > Service accounts > Generate new private key\n"
            "ile anahtarı bu klasöre kaydet (repoya girmez, .gitignore'da)."
        )

    cred = credentials.Certificate(str(SERVICE_ACCOUNT_PATH))
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print(f"[OK] Bağlanıldı: proje '{cred.project_id}'")

    # ---- oku -----------------------------------------------------------
    cards = []
    from google.cloud.firestore_v1 import FieldFilter

    for t in types:
        snap = db.collection("cards").where(filter=FieldFilter("type", "==", t)).stream()
        for d in snap:
            cards.append({"id": d.id, **d.to_dict()})
    if not cards:
        sys.exit("Kart bulunamadı.")

    # ---- ham döküm -----------------------------------------------------
    EXPORT_PATH.write_text(
        json.dumps(cards, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[OK] Ham döküm: {EXPORT_PATH} ({len(cards)} kart)")

    # ---- denetim -------------------------------------------------------
    findings = []

    def flag(cid, topic, msg):
        findings.append(f"- `{cid[:8]}` · {topic} · {msg}")

    fronts = defaultdict(list)
    for c in cards:
        cid = c.get("id", "?")
        topic = c.get("topic") or c.get("type", "?")
        front = (c.get("front") or "").strip()
        back = (c.get("back") or "").strip()
        detail = (c.get("detail") or "").strip()

        if not front:
            flag(cid, topic, "front boş")
        if not back:
            flag(cid, topic, "back boş")

        fronts[front.lower()].append(cid)

        # muhtemel çoktan seçmeli: şıklar var ama back'te doğru şık yok
        if re.search(r"(?:^|\s)[A-E]\) ", front) and not re.search(
            r"Doğru cevap|^[A-E]\)", back, flags=re.M
        ):
            flag(cid, topic, "çoktan seçmeli görünüyor ama back'te doğru şık belirtilmemiş")

        # kpss tipik alan şablonları: şüpheli hassasiyet
        if any(x in back for x in ("yaklaşık", "?", "belirsiz")):
            flag(cid, topic, f"belirsiz ifade back'te: “{(c.get('back') or '')[:60]}”")

    for f, ids in fronts.items():
        if len(ids) > 1:
            flag(ids[0], "—", f"aynı soru {len(ids)} kez: {ids}")

    topic_counts = Counter((c["type"], c.get("topic") or "(konusuz)") for c in cards)
    no_detail = sum(1 for c in cards if not (c.get("detail") or "").strip())
    detail_lens = sorted(len((c.get("detail") or "").strip()) for c in cards)
    median = detail_lens[len(detail_lens) // 2] if detail_lens else 0

    # ---- rapor ---------------------------------------------------------
    lines = [
        "# Kart gözden geçirme raporu",
        f"\nKaynak: Firestore `restudy-6e26e/cards` · {len(cards)} kart "
        f"({', '.join(f'{k[0]}/{k[1]}: {v}' for k, v in sorted(topic_counts.items()))})",
        f"\n`detail` dolu: {len(cards) - no_detail} · boş: {no_detail} · median uzunluk: {median} kr.",
        "\n## Denetim bulguları\n",
    ]
    lines.extend(findings or ["- (bulgu yok)"])
    lines.append("\n## Kart listesi\n")

    by_type_topic = defaultdict(list)
    for c in cards:
        by_type_topic[(c["type"], c.get("topic") or "(konusuz)")].append(c)

    for (t, topic), group in sorted(by_type_topic.items()):
        lines.append(f"\n### {t} / {topic} — {len(group)} kart\n")
        for c in group:
            lines.append(f"#### `{c['id'][:8]}` · {md_to_text(c.get('front') or '')[:110]}")
            lines.append(f"\n**Cevap:** {md_to_text(c.get('back') or '') or '_(boş)_'}")
            if (c.get("detail") or "").strip():
                lines.append(f"\n**Detail:** {md_to_text(c.get('detail'))[:600]}"
                             + ("… *(kesildi — tam metin cards_export.json'da)*"
                                if len(md_to_text(c.get("detail"))) > 600 else ""))
            lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Rapor: {REPORT_PATH}")
    if findings:
        print(f"[!] {len(findings)} bulgu — ayrıntı raporun 'Denetim bulguları' bölümünde.")


if __name__ == "__main__":
    main()
