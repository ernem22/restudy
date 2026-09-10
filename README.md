# Kartlar — kişisel aralıklı tekrar uygulaması

Notlarını / çıkmış soruları yerelde Gemini ile Q&A kartlarına çevirir,
Firestore'da saklar ve GitHub Pages'te barınan statik bir web arayüzünden
çalışırsın. Hiçbir yerde sunucu yok.

- `generate_cards.py` — notlardan Gemini ile kart **üretir**
- `import_kpss.py` — hazır soru-cevap içeriğinden (ÖSYM PDF / URL / dosya)
  kartları **aynen çıkarır**
- `check_firestore.py` — bir seferlik bağlantı testi
- `web/` — statik dosyalar (`index.html`, `style.css`, `app.js`,
  `firebase-config.js`), tüm mantık tarayıcıda döner
- Ayrıntılı mimari ve teknik spec için `HANDOFF.md`

Her kartın bir **türü** (`yazilim` veya `kpss`) ve bir **konusu** (serbest
metin, ör. `React`, `Tarih`) vardır. Web arayüzü 3 adımdır:
**1) tür seç → 2) konu seç → 3) o konunun hazır kartlarını çalış.**

---

## Çalıştırma (yerel)

```bash
cd web
python -m http.server 8000
```

- <http://localhost:8000/> → gerçek mod. Firebase'de oluşturduğun
  **e-posta + şifre** ile giriş yap.
- <http://localhost:8000/?demo> → Firebase baypas, örnek kartlarla arayüz
  denemesi (hiçbir şey kaydedilmez, giriş yok).

`web/` dosyalarını değiştirdiğinde `index.html` içindeki `?v=N` numaralarını
artır (`app.js?v=2`, `style.css?v=2`) — yoksa tarayıcı eski sürümü gösterir.

---

## Kart ekleme

```bash
pip install -r requirements.txt
cp .env.example .env      # içine GEMINI_API_KEY yaz
```

### A) Notlardan kart (`generate_cards.py`)

Kendi notlarından Gemini kart **üretir**. `--type` (`yazilim` | `kpss`) ve
`--topic` zorunlu.

```bash
python generate_cards.py notes/react_notlari.md --type yazilim --topic "React"
python generate_cards.py notes/kpss/tarih.md    --type kpss    --topic "Tarih"
```

### B) Hazır sorulardan kart (`import_kpss.py`)

Zaten soru-cevap olan içerikten kartları **aynen çıkarır** (soru yazmaz,
cevap uydurmaz). Kaynak: ÖSYM PDF, bir URL veya kaydettiğin yerel dosya
(`.html` / `.txt` / `.md`). Hem çoktan seçmeli hem açık uçlu biçimi işler.

```bash
# önce --dry-run ile ne çıkacağını gör (Firestore'a yazmaz)
python import_kpss.py notes/kpss/gy-gk_2024.pdf --topic "GY-GK 2024" --dry-run
python import_kpss.py https://site.com/kpss/tarih --topic "Tarih" --dry-run

# iyi görünüyorsa --dry-run'ı kaldır
python import_kpss.py notes/kpss/kaydettigim.html --topic "Tarih"
```

Bayraklar:

- **`--vision`** — PDF/görüntüyü metne çevirmeden doğrudan Gemini'ye verir
  (OCR). Görsel-ağırlıklı ÖSYM PDF'leri, taranmış sayfalar, ekran
  görüntüleri için — metin modundan çok daha temiz. Biraz pahalı/yavaş,
  yerel dosya ister.
- **`--auto-topic`** — her kartı Gemini'nin belirlediği derse yazar
  (Tarih / Coğrafya / Türkçe / Matematik / Vatandaşlık / Güncel Bilgiler).
  Karışık ÖSYM GY-GK kitapçıkları için ideal. `--topic` yedek kalır.
- **`--keep-suspect`** — `⚠ ŞÜPHELİ` (metni bozuk) kartlar normalde atlanır,
  bununla yazılır.
- **`--limit N`** — en fazla N kart.

```bash
python import_kpss.py notes/kpss/gy-gk_2024.pdf --topic "GY-GK 2024" --vision --auto-topic --dry-run
```

Notlar:

- Aynı konuya ikinci kez import edersen kopyalar (soru metnine göre) atlanır.
- ÖSYM sadece sınavın %10'unu yayımlar (~12 soru). Matematik soruları görsel
  → metin modunda çıkmaz, `--vision` gerekir.
- Blog tipi "Soru/Cevap" sayfaları (ör. tarihvakti.com) temiz çıkar; ifade
  adayların hatırlamasıyla yazıldığı için resmi değildir.
- Ticari yayınevi soru bankaları / korsan doküman siteleri **kapsam dışı**.
- Kaynak ayrıntıları: `HANDOFF.md` §9.

### Ortak

Her yeni kart hemen "due" olur, web arayüzünde ilgili tür + konu altında
görünür. Konu adı serbest metin — `"Tarih"` ve `"tarih"` iki ayrı konu olur,
tutarlı yaz.

---

## Sıfırdan Firebase kurulumu

Bu repo zaten çalışan bir `web/firebase-config.js` ve yayımlanmış
`firestore.rules` ile geliyor. Aşağıdakiler **yeni bir Firebase projesi**
bağlamak istersen gereklidir.

### 1. Firebase projesi

1. [console.firebase.google.com](https://console.firebase.google.com) → yeni proje.
2. **Firestore Database** → **Create database** → **production mode**.
3. **Authentication** → **Get started** → **Sign-in method** →
   **Email/Password** sağlayıcısını etkinleştir.
4. **Authentication** → **Users** → **Add user** → e-posta + şifre.
   Oluşan kullanıcının **UID**'sini kopyala.

### 2. Güvenlik kuralları

1. `firestore.rules` içindeki UID'yi 1.4'te kopyaladığınla değiştir.
2. Firebase konsolu → **Firestore Database** → **Rules** → dosyanın
   içeriğini yapıştır → **Publish**.

Sonuç: site ve Firebase config herkese açık olsa da, Firestore o tek UID ile
kimlik doğrulanmamış her okuma/yazmayı reddeder.

### 3. Web config

**Project settings** → **General** → **Your apps** → **Web app** → gösterilen
`firebaseConfig` değerlerini `web/firebase-config.js` içine yaz. Bu değerler
gizli değildir, commit edilebilir.

### 4. Service account (yalnızca yerel script için)

**Project settings** → **Service accounts** → **Generate new private key** →
inen dosyayı script'lerin yanına `serviceAccountKey.json` olarak kaydet.
**Asla commit etme** — `.gitignore`'da.

### 5. Gemini API anahtarı

[aistudio.google.com](https://aistudio.google.com) → **Get API key** →
`.env` içine `GEMINI_API_KEY=...`.

### 6. Doğrula

```bash
python check_firestore.py
```

Yaz → oku → sil zincirini test eder. Hepsi `[OK]` ise kurulum tamam.

---

## Yayınlama (GitHub Pages)

GitHub Pages "Deploy from a branch" yalnızca **kök** veya **`/docs`**
klasörünü sunabilir — `/web` seçeneği yoktur. İki yoldan biri:

**A) `web/` → `docs/` olarak yeniden adlandır** (en kolay):

1. `web` klasörünü `docs` yap, push et.
2. Repo → **Settings** → **Pages** → **Source: Deploy from a branch** →
   Branch: `main`, klasör: `/docs` → **Save**.

**B) GitHub Actions** ile `web/` klasörünü yükleyen bir Pages workflow'u.

Ayrıca Firebase konsolu → **Authentication** → **Settings** →
**Authorized domains**'e `<kullanıcı>.github.io` ekle.

---

## Kullanım

1. **Tür seç** — Yazılım veya KPSS (kaç kart hazır yazar).
2. **Konu seç** — o türdeki konular, "hazır / toplam" sayısıyla. Bitmiş
   konular ✓ ile soluk.
3. **Çalış** — soru → **Cevabı göster** (boşluk tuşu) → **Tekrar / Zor /
   İyi / Kolay** (1–4 tuşları). SM-2 sonraki tekrarı hesaplar. "← Konu" ile
   başka konuya geç.
