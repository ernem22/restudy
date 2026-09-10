# restudy

Turns notes into flashcards so my brain recalls them.

Aralıklı tekrar (SM-2) kart uygulaması. `generate_cards.py` / `import_kpss.py`
yerelde notları ve çıkmış soruları Gemini ile karta çevirip Firestore'a yazar;
`web/` bunları GitHub Pages'te barınan statik bir arayüzden sunar.

- Yazılan/deploy edilen sunucu kodu yok — arka uç Firebase (Firestore + Auth),
  tüm mantık tarayıcıda.
- Tek kullanıcılık: `firestore.rules` tek bir Firebase UID'ne kilitli.
- Kart modeli: her kartın bir **türü** (`yazilim` | `kpss`) ve bir **konusu**
  (serbest metin) var. Arayüz: tür → konu → o konunun due kartları. Notlama
  (Tekrar / Zor / İyi / Kolay) sonraki tekrar tarihini belirler.

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
