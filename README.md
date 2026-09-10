# Kartlar — kişisel aralıklı tekrar uygulaması

Notlarını yerelde Gemini ile Q&A kartlarına çevirir, Firestore'da saklar ve
GitHub Pages'te barınan statik bir web arayüzünden çalışırsın. Hiçbir yerde
sunucu yok.

- `generate_cards.py` — notlardan Gemini ile kart **üretir**
- `import_kpss.py` — hazır soru-cevap içeriğinden (ÖSYM PDF / URL / dosya)
  kartları **aynen çıkarır**
- `check_firestore.py` — bir seferlik bağlantı testi
- `web/` — 3 statik dosya (`index.html`, `style.css`, `app.js`), tüm mantık
  tarayıcıda döner
- Ayrıntılı mimari ve teknik spec için `HANDOFF.md`

Her kartın bir **türü** (`yazilim` veya `kpss`) ve bir **konusu** (serbest
metin, ör. `React`, `Anayasa Hukuku`) vardır. Web arayüzü 3 adımdır:
**1) tür seç → 2) konu seç → 3) o konunun hazır kartlarını çalış.**

## Kurulum

### 1. Firebase projesi

1. [console.firebase.google.com](https://console.firebase.google.com) → yeni proje oluştur.
2. **Firestore Database** → **Create database** → **production mode** (test mode değil).
3. **Authentication** → **Get started** → **Sign-in method** → **Email/Password**
   sağlayıcısını etkinleştir.
4. **Authentication** → **Users** → **Add user** → kendi e-postan + bir şifre.
   Oluşan kullanıcının **UID**'sini kopyala.

### 2. Güvenlik kurallarını yayınla

1. `firestore.rules` dosyasını aç, `PASTE_YOUR_UID_HERE` yerine 1.4'te
   kopyaladığın UID'yi yapıştır.
2. Firebase konsolu → **Firestore Database** → **Rules** sekmesi → dosyanın
   içeriğini yapıştır → **Publish**.

Sonuç: site ve Firebase config herkese açık olsa da, Firestore o tek UID ile
kimlik doğrulanmamış her okuma/yazmayı reddeder.

### 3. Web config

1. Firebase konsolu → **Project settings** (dişli) → **General** → aşağıda
   **Your apps** → **Web app** ekle (`</>` ikonu).
2. Gösterilen `firebaseConfig` değerlerini `web/firebase-config.js` içindeki
   `PASTE_ME` alanlarına yapıştır. Bu değerler gizli değildir, commit edilebilir.

### 4. Service account (yalnızca yerel script için)

1. Firebase konsolu → **Project settings** → **Service accounts** →
   **Generate new private key**.
2. İnen dosyayı `generate_cards.py` ile aynı klasöre `serviceAccountKey.json`
   adıyla kaydet. **Asla commit etme** — zaten `.gitignore`'da.

### 5. Gemini API anahtarı

1. [aistudio.google.com](https://aistudio.google.com) → **Get API key**.
2. `.env.example`'ı `.env` olarak kopyala, anahtarı içine yaz:
   ```
   GEMINI_API_KEY=anahtarın
   ```
   `.env` `.gitignore`'da — repoya girmez. Script'ler otomatik okur.

## Kart üretme

```bash
pip install -r requirements.txt
```

### A) Notlardan kart (`generate_cards.py`)

Kendi notlarından Gemini kart **üretir**. `--type` (`yazilim` | `kpss`) ve
`--topic` zorunlu.

```bash
python generate_cards.py notes/react_notlari.md --type yazilim --topic "React"
python generate_cards.py notes/kpss/tarih.md    --type kpss    --topic "Tarih"
```

### B) Hazır sorulardan kart (`import_kpss.py`)

Zaten soru-cevap olan içerikten kartları **aynen çıkarır** (soru yazmaz, cevap
uydurmaz). Kaynak: ÖSYM PDF, bir URL veya kaydettiğin yerel dosya.

```bash
# önce --dry-run ile ne çıkacağını gör (Firestore'a yazmaz)
python import_kpss.py notes/kpss/gy-gk_2022.pdf --topic "GY-GK 2022" --dry-run
python import_kpss.py https://site.com/kpss/anayasa --topic "Anayasa" --dry-run

# iyi görünüyorsa --dry-run'ı kaldır
python import_kpss.py notes/kpss/kaydettigim.html --topic "Anayasa"
```

**`--vision`** — PDF/görüntüyü metne çevirmeden doğrudan Gemini'ye verir (OCR).
Görsel-ağırlıklı ÖSYM PDF'leri ve ekran görüntüleri için; metin modundan çok
daha temiz. Biraz pahalı/yavaş, yerel dosya ister.

**`--auto-topic`** — her kartı Gemini'nin belirlediği derse yazar (Tarih,
Coğrafya, Türkçe, Matematik, Vatandaşlık, Güncel Bilgiler). Karışık ÖSYM
GY-GK kitapçıkları için ideal — tek dosyadan dersler ayrı topic'lere düşer.
`--topic` ders bulunamayan kartlar için yedek kalır.

```bash
python import_kpss.py notes/kpss/gy-gk_2024.pdf --topic "GY-GK 2024" --vision --auto-topic --dry-run
```

- `⚠ ŞÜPHELİ` işaretli kartlar (metni bozuk) gerçek yazımda atlanır;
  `--keep-suspect` ile zorlayabilirsin.
- Aynı konuya ikinci kez import edersen kopyalar atlanır (soru metnine göre).
- ÖSYM PDF'leri sadece sınavın %10'unu içerir. Metin modunda matematik çıkmaz,
  düzen karışabilir → `--vision` kullan. Detay: `HANDOFF.md` §9.

### Ortak

Her yeni kart hemen "due" olur, web arayüzünde ilgili tür + konu altında görünür.
Konu adı serbest metin — `"Tarih"` ve `"tarih"` iki ayrı konu olur, tutarlı yaz.

## Yayınlama (GitHub Pages)

GitHub Pages "Deploy from a branch" yalnızca **kök** veya **`/docs`** klasörünü
sunabilir — `/web` seçeneği yoktur. İki yoldan biri:

**A) `web/` → `docs/` olarak yeniden adlandır** (en kolay):

1. `web` klasörünü `docs` yap, repoyu push et.
2. Repo → **Settings** → **Pages** → **Source: Deploy from a branch** →
   Branch: `main`, klasör: `/docs` → **Save**.

**B) GitHub Actions** ile `web/` klasörünü artifact olarak yükleyen bir
Pages workflow'u ekle (biraz daha uğraş).

Birkaç dakika sonra verilen Pages URL'sini aç, oluşturduğun kullanıcıyla
giriş yap.

## Kullanım

1. **Tür seç** — Yazılım veya KPSS (her birinde kaç kart hazır olduğu yazar).
2. **Konu seç** — o türdeki konular, yanlarında "hazır / toplam" sayısı.
   Bitmiş konular ✓ ile soluk görünür.
3. **Çalış** — soru gösterilir → **Cevabı göster** (veya boşluk tuşu) →
   kendini değerlendir: **Tekrar / Zor / İyi / Kolay** (veya 1–4 tuşları).
   SM-2 bir sonraki tekrar tarihini hesaplar ve Firestore'a yazar.
   Konu bitince "← Konu" ile başka konuya geçebilirsin.

`?demo` — `https://.../?demo` ile açarsan Firebase baypas edilir, örnek
kartlarla arayüzü denersin (hiçbir şey kaydedilmez).
