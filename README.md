# restudy

**Turns notes into flashcards so my brain recalls them.**

Kişisel aralıklı tekrar (spaced repetition) uygulaması. Notlarımı ve çıkmış
soruları yerelde Gemini ile soru-cevap kartlarına çeviririm, Firestore'a
yazarım, kartları GitHub Pages'teki statik bir web arayüzünden çalışırım.

Tek kullanıcılık — `firestore.rules` bir Firebase UID'ne kilitli. Yayındaki
site herkese açık görünür ama kartları sadece o hesap (şifreyle) görebilir.

---

## Nasıl çalışıyor

```
  notlar / ÖSYM PDF / URL            Firestore              GitHub Pages
        │                          ┌─────────────┐        ┌───────────────┐
  generate_cards.py  ───── yaz ──▶ │  cards      │ ◀────▶ │ web/ (statik) │
  import_kpss.py                   │  front/back │  Fire- │ auth, okuma,  │
  (yerel · Gemini +                │  SM-2 state │  base  │ SM-2, yazma   │
   admin SDK)                      └─────────────┘  JS SDK │ hepsi browser │
                                                          └───────────────┘
```

- **Yazıp deploy edilen sunucu kodu yok.** Arka uç = Firebase'in yönettiği
  Firestore + Auth. Web arayüzü salt statik dosya, tüm mantık tarayıcıda.
- Kart üretimi yerelde, elle çalıştırılan Python script'leriyle.

## Kart modeli

Her kartın bir **türü** (`yazilim` | `kpss`) ve bir **konusu** (serbest metin,
ör. `React`, `Tarih`) var. Arayüz 3 adım:
**tür seç → konu seç → o konunun hazır kartlarını çalış.**
Notlama (Tekrar / Zor / İyi / Kolay) SM-2 ile sonraki tekrar tarihini hesaplar.

---

## Çalıştırma (yerel)

```bash
cd web && python -m http.server 8000
```

| URL | |
|---|---|
| `localhost:8000/` | gerçek mod — Firebase hesabıyla giriş |
| `localhost:8000/?demo` | Firebase baypas, örnek kartlar, giriş yok |

`web/` dosyalarını değiştirince `index.html` içindeki `?v=N` numaralarını
artır (`app.js?v=2`, `style.css?v=2`) — yoksa tarayıcı eskisini gösterir.

## Kart ekleme

```bash
pip install -r requirements.txt
cp .env.example .env        # içine GEMINI_API_KEY
```

**Notlardan üret** — Gemini notu okuyup kart yazar:

```bash
python generate_cards.py notes/react.md --type yazilim --topic "React"
```

**Hazır sorulardan çıkar** — Gemini soruyu aynen aktarır, uydurmaz. Kaynak:
ÖSYM PDF, URL veya yerel `.html`/`.txt`/`.md`. Çoktan seçmeli + açık uçlu.

```bash
python import_kpss.py notes/kpss/gy-gk_2024.pdf --topic "GY-GK 2024" --dry-run
```

`import_kpss.py` bayrakları: `--vision` (PDF/görüntüyü doğrudan Gemini'ye,
OCR — taranmış / görsel-ağırlıklı kaynaklar için), `--auto-topic` (her kartı
dersine göre ayrı konuya yazar), `--dry-run` (yazmadan göster),
`--keep-suspect`, `--limit N`. Aynı konuya tekrar import kopyaları atlar.

`check_firestore.py` — yaz/oku/sil zincirini test eder, bağlantıyı doğrular.

---

## Kendine kurmak istersen

### 1 · Firebase

1. [console.firebase.google.com](https://console.firebase.google.com) → yeni proje
2. **Firestore Database** → Create (production mode)
3. **Authentication** → Email/Password sağlayıcısını aç →
   **Users** → Add user (e-posta + şifre) → **UID**'yi kopyala
4. `firestore.rules` içindeki UID'yi kendininkiyle değiştir →
   konsol **Firestore → Rules** → yapıştır → **Publish**
5. **Project settings → General → Your apps → Web app** →
   `firebaseConfig` değerlerini `web/firebase-config.js` içine yaz
   (gizli değil, commit edilebilir)
6. **Project settings → Service accounts → Generate new private key** →
   script'lerin yanına `serviceAccountKey.json` (asla commit etme)

### 2 · Gemini

[aistudio.google.com](https://aistudio.google.com) → Get API key →
`.env` içine `GEMINI_API_KEY=...`

### 3 · Deploy (GitHub Pages)

`.github/workflows/pages.yml` `web/` klasörünü Pages'e yükler.

1. Repo → **Settings → Pages → Source: GitHub Actions**
2. `main`'e push (`web/**` değişmişse) → **Actions** sekmesinden izle,
   veya "Run workflow" ile elle
3. Firebase → **Authentication → Settings → Authorized domains** →
   `<kullanıcı>.github.io` ekle *(yoksa yayında giriş çalışmaz)*

Yayın: `https://<kullanıcı>.github.io/restudy/`

---

## Yığın

Statik HTML/CSS/JS (framework yok) · Firebase v10 modüler SDK (CDN) ·
Firestore · Firebase Auth · Gemini API (`gemini-3.1-flash-lite`) ·
Python (`firebase-admin`, `pymupdf`) · SM-2 aralıklı tekrar.

Teknik detay ve tasarım kararları: [`HANDOFF.md`](HANDOFF.md).
