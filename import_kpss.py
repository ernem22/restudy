#!/usr/bin/env python3
"""
import_kpss.py — HAZIR soru-cevap içeriğini alıp Firestore'a `type=kpss`
kartları olarak yazar. İki biçimi de işler: çoktan seçmeli (soru + A–E şık +
işaretli cevap) ve açık uçlu ("Soru: ... / Cevap: ...").

generate_cards.py'den farkı: bu script soru ÜRETMEZ. Verilen metindeki
soruları AYNEN çıkarır. Gemini sadece dağınık metni {front, back} biçimine
sokar — soru yazmaz, cevap uydurmaz, eksik/görsel soruyu atlar.

Kaynak (otomatik algılanır):
    python import_kpss.py notes/kpss/gy-gk_2022.pdf   --topic "Tarih"     # ÖSYM PDF (metin)
    python import_kpss.py https://site.com/kpss/tarih  --topic "Tarih"     # web sayfası
    python import_kpss.py notes/kpss/kaydettigim.html  --topic "Tarih"     # yerel dosya

Bayraklar:
    --topic "..."   zorunlu, konu adı (web arayüzü 2. adımda gösterir)
    --dry-run       Firestore'a YAZMA, sadece çıkacak kartları göster
    --limit N       en fazla N kart yaz (test için)
    --keep-suspect  düzen bozukluğu şüpheli kartları da yaz
    --vision        PDF/görüntüyü doğrudan Gemini'ye ver (OCR). Görsel-ağırlıklı
                    ÖSYM PDF'leri, taranmış sayfalar, ekran görüntüleri için —
                    metin modundan çok daha temiz sonuç verir, biraz pahalı/yavaş.
                    Yerel dosya ister (PDF/PNG/JPG).

Kurulum: bkz. README. Anahtar `.env` içindeki GEMINI_API_KEY'den okunur.

NOT: ÖSYM ve soru paylaşan siteler sorular için telif iddia eder. Bu araç
kişisel çalışma içindir — ürettiğin kart veritabanını yayımlama, paylaşma,
repoya commit'leme. Kaynak seçimi ve ilgili sitenin kullanım şartları senin
sorumluluğundadır; script tek seferde tek kaynak işler, oturum açmaz.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import FieldFilter
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
SERVICE_ACCOUNT_PATH = Path(__file__).parent / "serviceAccountKey.json"
CARD_TYPE = "kpss"
CHUNK_CHARS = 6000  # hazır sorular yoğun; birkaç soru bir arada dursun
USER_AGENT = "restudy-kpss-import/1.0 (kisisel calisma araci)"

# --auto-topic ile her kart kendi dersine göre ayrı topic'e yazılır
KPSS_SUBJECTS = [
    "Türkçe", "Matematik", "Tarih", "Coğrafya", "Vatandaşlık",
    "Güncel Bilgiler", "Eğitim Bilimleri",
]

SYSTEM_PROMPT = """Sana Türkçe çoktan seçmeli sınav soruları içeren bir metin \
verilecek. Metinde sorular, şıkları ve çoğu zaman doğru cevaplar da bulunur \
(soru içinde "DOĞRU CEVAP: X" gibi ya da ayrı bir cevap anahtarında).

Görevin: metindeki her TAM soruyu AYNEN çıkarmak.

Metin iki biçimden birinde olabilir:
  (a) Çoktan seçmeli: soru kökü + A–E şıkları + işaretli doğru cevap.
  (b) Açık uçlu: "Soru: ... / Cevap: ..." gibi kısa soru-cevap.

Kurallar — hepsine uy:
1. "front" = soru kökü.
   - (a) ise şıkları da ekle, her biri yeni satırda: "A) ...", "B) ..." vb.
   - (b) ise sadece soru kökü.
   Metindeki ifadeyi değiştirme, kısaltma, düzeltme.
2. "back":
   - (a) ise: "Doğru cevap: X) <o şıkkın metni>" (X metinde işaretli harf).
   - (b) ise: cevabın metni, olduğu gibi.
3. Soruyu ASLA yeniden yazma, özetleme, yorumlama, kendin çözme veya YENİ
   soru uydurma. Yalnızca yazılanı aktar.
4. Şu durumlarda o soruyu ATLA (kart üretme):
   - (a) türünde bir şık eksikse,
   - "işleminin sonucu", "aşağıdaki şekil/grafik/tablo/görsel", "yukarıdaki
     ifade" gibi metinde OLMAYAN bir içeriğe atıf yapıyorsa,
   - metin bozuksa: kelimeler birbirine yapışmış ("zamankullanılamaz"),
     cümle ortadan kesilmiş, harf/sözcük karışmışsa,
   - cevap metinde hiçbir yerde belirtilmemişse.
   Emin değilsen ATLA. Az ama temiz kart, çok ama bozuk karttan iyidir.
5. Aynı soruyu iki kez yazma.
6. "konu": sorunun KPSS dersi. Şunlardan biri: Türkçe, Matematik, Tarih,
   Coğrafya, Vatandaşlık, Güncel Bilgiler, Eğitim Bilimleri. Emin değilsen
   "Diğer" yaz.
7. Çıktı: SADECE bir JSON dizisi, öncesinde/sonrasında hiçbir metin olmadan:
   [{"front": "...", "back": "...", "konu": "..."}]
8. Çıkarılacak sağlam soru yoksa [] döndür.
"""


# ---- kaynak okuma ---------------------------------------------------

def read_pdf(path: Path) -> str:
    import pymupdf

    doc = pymupdf.open(str(path))
    # sort=True: iki kolonlu ÖSYM düzenini konuma göre sıralar
    parts = [page.get_text("text", sort=True) for page in doc]
    doc.close()
    return "\n".join(parts)


def read_html(markup: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def read_url(url: str) -> str:
    import requests

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        sys.exit(f"Sayfa alınamadı ({e.__class__.__name__}): {url}")
    ctype = resp.headers.get("content-type", "")
    if "html" in ctype or resp.text.lstrip().lower().startswith("<!doctype html"):
        return read_html(resp.text)
    return resp.text


def load_source(src: str) -> str:
    if src.startswith(("http://", "https://")):
        print(f"Web sayfası çekiliyor: {src}")
        return read_url(src)

    path = Path(src)
    if not path.exists():
        sys.exit(f"Kaynak bulunamadı: {src}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        print(f"PDF okunuyor: {path}")
        return read_pdf(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        return read_html(text)
    return text  # .txt / .md / diğer düz metin


# ---- Gemini ile yapılandırma --------------------------------------

def chunk_text(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
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


def _parse_cards(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[4:] if raw.lower().startswith("json") else raw
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ValueError("JSON dizi bekleniyordu")
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("front") and it.get("back"):
            front = str(it["front"]).strip()
            # baştaki "Soru 12)" / "12." / "12)" gibi numaraları at
            front = re.sub(r"^\s*(?:soru\s*)?\d{1,3}\s*[.):]\s*", "", front,
                           flags=re.IGNORECASE).strip()
            konu = str(it.get("konu", "")).strip()
            out.append({
                "front": front,
                "back": str(it["back"]).strip(),
                "konu": konu if konu in KPSS_SUBJECTS else "",
            })
    return out


def call_gemini(model, parts, retries: int = 3) -> list[dict]:
    """parts: metin parçası (str) veya [yüklenmiş dosya, str] listesi."""
    if isinstance(parts, str):
        parts = [f"{SYSTEM_PROMPT}\n\n---\nMETİN:\n{parts}\n---"]
    for attempt in range(1, retries + 1):
        try:
            return _parse_cards(model.generate_content(parts).text)
        except Exception as e:
            print(f"    [deneme {attempt}/{retries}] hata: {e}", file=sys.stderr)
            time.sleep(2 * attempt)
    print("    bu parça atlandı", file=sys.stderr)
    return []


def extract_vision(model, path: Path) -> list[dict]:
    """PDF/görüntüyü doğrudan Gemini'ye ver — OCR + soru çıkarma tek adımda."""
    print(f"Gemini'ye yükleniyor (vision): {path.name}")
    handle = genai.upload_file(str(path))
    while getattr(handle.state, "name", "ACTIVE") == "PROCESSING":
        time.sleep(2)
        handle = genai.get_file(handle.name)
    if getattr(handle.state, "name", "ACTIVE") == "FAILED":
        sys.exit("Gemini dosyayı işleyemedi.")
    prompt = (
        SYSTEM_PROMPT
        + "\n\nYukarıdaki belge/görüntüdeki TÜM soruları çıkar. Görseldeki "
        "matematik ifadelerini/tabloları metne dök; okunamayan kısımlar için "
        "o soruyu atla."
    )
    try:
        cards = call_gemini(model, [handle, prompt])
    finally:
        try:
            genai.delete_file(handle.name)
        except Exception:
            pass
    return cards


# ---- dedup ---------------------------------------------------------

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


# kaba metin bozukluğu sezgisi (kesin değil, sadece göze batsın diye):
# küçük+büyük harf yapışması ("göreMşekillenen") veya harf.harf boşluksuz
# ("zamankullanılamaz.ile"). Çoktan seçmeli kartta A–E'den biri eksikse de
# şüpheli — ama açık uçlu (şıksız) kartlar bundan muaf.
_FUSED = re.compile(r"[a-zçğıöşü][A-ZÇĞİÖŞÜ]|[a-zçğıöşü]\.[a-zçğıöşü]")


def looks_broken(card: dict) -> bool:
    front = card["front"]
    if _FUSED.search(front):
        return True
    # blog kaynaklarında belirsiz "... sorusu" / "... sorusu vardı" girdileri
    if re.search(r"soru(?:su)?(?:\s+vard[ıi])?[\s.]*$", front, re.IGNORECASE):
        return True
    if len(front) < 15:  # gerçek bir soru kökü değil
        return True
    labels = set(re.findall(r"(?m)^\s*([A-E])\)", front))
    if not labels:
        return False  # açık uçlu kart, şık beklenmiyor
    return not {"A", "B", "C", "D", "E"} <= labels  # kısmi şık = bozuk


def existing_keys(db) -> set[tuple[str, str]]:
    """(topic, normalize edilmiş front) — tüm kpss kartları için, dedup amaçlı."""
    q = db.collection("cards").where(filter=FieldFilter("type", "==", CARD_TYPE))
    out = set()
    for d in q.stream():
        x = d.to_dict()
        out.add((x.get("topic", ""), norm(x.get("front", ""))))
    return out


# ---- main --------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="ÖSYM PDF yolu, URL veya yerel dosya")
    parser.add_argument("--topic", required=True, help='konu, ör. "Tarih"')
    parser.add_argument("--dry-run", action="store_true",
                        help="Firestore'a yazma, sadece göster")
    parser.add_argument("--limit", type=int, default=0,
                        help="en fazla N kart yaz (0 = sınırsız)")
    parser.add_argument("--keep-suspect", action="store_true",
                        help="düzen bozukluğu şüpheli kartları da yaz")
    parser.add_argument("--vision", action="store_true",
                        help="PDF/görüntüyü Gemini'ye ver (OCR). Görsel-ağırlıklı "
                             "ÖSYM PDF'leri ve ekran görüntüleri için.")
    parser.add_argument("--auto-topic", action="store_true",
                        help="her kartı Gemini'nin belirlediği derse yaz "
                             "(Tarih/Coğrafya/...). Karışık ÖSYM kitapçıkları için. "
                             "--topic ders bulunamayan kartlar için yedek.")
    args = parser.parse_args()

    topic = args.topic.strip()
    if not topic:
        sys.exit("--topic boş olamaz.")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY yok. .env dosyasına ekle (bkz. .env.example).")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        generation_config={"response_mime_type": "application/json"},
    )

    cards, seen = [], set()

    def add(new):
        for c in new:
            key = norm(c["front"])
            if key and key not in seen:
                seen.add(key)
                cards.append(c)

    if args.vision:
        path = Path(args.source)
        if not path.exists():
            sys.exit(f"--vision yerel bir dosya ister: {args.source}")
        add(extract_vision(model, path))
    else:
        text = load_source(args.source)
        if len(text.strip()) < 40:
            sys.exit("Kaynaktan anlamlı metin çıkmadı — taranmış/görsel PDF "
                     "olabilir, --vision dene.")
        chunks = chunk_text(text)
        print(f"{len(text)} karakter, {len(chunks)} parça. Ayrıştırılıyor...\n")
        for i, chunk in enumerate(chunks, start=1):
            got = call_gemini(model, chunk)
            add(got)
            print(f"  parça {i}/{len(chunks)}: +{len(got)} (benzersiz {len(cards)})")

    if not cards:
        sys.exit("\nÇıkarılabilir soru bulunamadı.")

    if args.limit:
        cards = cards[: args.limit]

    suspect = sum(1 for c in cards if looks_broken(c))

    def topic_of(c):
        return c["konu"] if (args.auto_topic and c.get("konu")) else topic

    if args.dry_run:
        print(f"\n--- DRY RUN: {len(cards)} kart (yazılmadı)"
              + (f", {suspect} şüpheli ⚠" if suspect else "") + " ---\n")
        for n, c in enumerate(cards, start=1):
            mark = " ⚠ ŞÜPHELİ (metin bozuk olabilir)" if looks_broken(c) else ""
            print(f"[{n}] ({topic_of(c)}){mark}\n{c['front']}\n    → {c['back']}\n")
        if args.auto_topic:
            from collections import Counter
            dist = Counter(topic_of(c) for c in cards)
            print("Ders dağılımı:", dict(dist))
        return

    if suspect and not args.keep_suspect:
        print(f"{suspect} şüpheli kart atlanacak (--keep-suspect ile yazdırabilirsin).")
        cards = [c for c in cards if not looks_broken(c)]

    if not SERVICE_ACCOUNT_PATH.exists():
        sys.exit(f"Eksik: {SERVICE_ACCOUNT_PATH}")
    cred = credentials.Certificate(str(SERVICE_ACCOUNT_PATH))
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    already = existing_keys(db)
    src = Path(args.source).name if not args.source.startswith("http") else args.source
    now = datetime.now(timezone.utc)
    from collections import Counter
    written, skipped = Counter(), 0
    for c in cards:
        ct = topic_of(c)
        if (ct, norm(c["front"])) in already:
            skipped += 1
            continue
        already.add((ct, norm(c["front"])))
        db.collection("cards").add({
            "front": c["front"],
            "back": c["back"],
            "type": CARD_TYPE,
            "topic": ct,
            "tags": [CARD_TYPE, ct],
            "source": src,
            "createdAt": now,
            "repetitions": 0,
            "easeFactor": 2.5,
            "interval": 0,
            "nextReview": now,
            "lastReviewed": None,
        })
        written[ct] += 1

    total = sum(written.values())
    print(f"\nBitti. {total} kart yazıldı"
          + (f", {skipped} kopya atlandı" if skipped else "") + ".")
    for t, n in sorted(written.items()):
        print(f"  kpss / {t}: +{n}")


if __name__ == "__main__":
    main()
