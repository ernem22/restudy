# restudy

Turns notes into flashcards so my brain recalls them.

A spaced-repetition (SM-2) flashcard app. `generate_cards.py` /
`import_kpss.py` run locally, turning notes and past exam questions into cards
with Gemini and writing them to Firestore; `web/` serves them from a static
site on GitHub Pages.

- No server code to write or deploy — the backend is Firebase (Firestore +
  Auth); all logic runs in the browser.
- Single-user: `firestore.rules` is locked to one Firebase UID.
- Card model: every card has a **type** (`yazilim` | `kpss`) and a **topic**
  (free text). The UI is type → topic → that topic's due cards. Grading
  (Again / Hard / Good / Easy) sets the next review date.

---

## Run it (local)

```bash
cd web && python -m http.server 8000
```

| URL | |
|---|---|
| `localhost:8000/` | real mode — sign in with the Firebase account |
| `localhost:8000/?demo` | Firebase bypassed, sample cards, no sign-in |

When you change files in `web/`, bump the `?v=N` on the assets in
`index.html` (`app.js?v=2`, `style.css?v=2`) or the browser serves the stale
version.

## Adding cards

```bash
pip install -r requirements.txt
cp .env.example .env        # put your GEMINI_API_KEY in it
```

**Generate from notes** — Gemini reads the note and writes cards:

```bash
python generate_cards.py notes/react.md --type yazilim --topic "React"
```

**Extract from ready Q&A** — Gemini copies questions verbatim, never invents.
Source: an ÖSYM PDF, a URL, or a local `.html`/`.txt`/`.md`. Multiple-choice
and open-ended both work.

```bash
python import_kpss.py notes/kpss/gy-gk_2024.pdf --topic "GY-GK 2024" --dry-run
```

`import_kpss.py` flags: `--vision` (send the PDF/image straight to Gemini for
OCR — for scanned / image-heavy sources), `--auto-topic` (write each card to
its own subject topic), `--dry-run` (show without writing), `--keep-suspect`,
`--limit N`. Re-importing the same topic skips duplicates.

`check_firestore.py` — write/read/delete round-trip to verify the connection.

---

## Setting it up yourself

### 1 · Firebase

1. [console.firebase.google.com](https://console.firebase.google.com) → new project
2. **Firestore Database** → Create (production mode)
3. **Authentication** → enable the Email/Password provider →
   **Users** → Add user (email + password) → copy its **UID**
4. Replace the UID in `firestore.rules` with yours →
   console **Firestore → Rules** → paste → **Publish**
5. **Project settings → General → Your apps → Web app** →
   put the `firebaseConfig` values into `web/firebase-config.js`
   (not secret, safe to commit)
6. **Project settings → Service accounts → Generate new private key** →
   save as `serviceAccountKey.json` next to the scripts (never commit)

### 2 · Gemini

[aistudio.google.com](https://aistudio.google.com) → Get API key →
`GEMINI_API_KEY=...` in `.env`

### 3 · Deploy (GitHub Pages)

`.github/workflows/pages.yml` uploads `web/` to Pages.

1. Repo → **Settings → Pages → Source: GitHub Actions**
2. Push to `main` (when `web/**` changed) → watch the **Actions** tab, or
   trigger it manually with "Run workflow"
3. Firebase → **Authentication → Settings → Authorized domains** → add
   `<user>.github.io` *(without this, sign-in fails on the live site)*

Live at `https://<user>.github.io/restudy/`

---

## Stack

Static HTML/CSS/JS (no framework) · Firebase v10 modular SDK (CDN) ·
Firestore · Firebase Auth · Gemini API (`gemini-3.1-flash-lite`) ·
Python (`firebase-admin`, `pymupdf`) · SM-2 spaced repetition.

Technical detail and design decisions: [`HANDOFF.md`](HANDOFF.md).
