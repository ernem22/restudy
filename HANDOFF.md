# restudy — Handoff Spec

Personal, single-user spaced-repetition flashcard app. Notes are turned into
Q&A cards locally by Gemini, stored in Firestore, studied through a static
web UI hosted on GitHub Pages. No **self-hosted** backend: the backend is
Firebase's managed Firestore + Auth; nothing else runs server code.

Every card belongs to exactly one **type** (`yazilim` or `kpss`) and one
free-text **topic** (konu), e.g. `React`, `Anayasa Hukuku`. The web app is a
3-step flow: **1) pick type → 2) pick topic → 3) study that topic's due
cards.** `generate_cards.py` requires `--type` and `--topic` on every run.

## 1. Architecture

```
┌─────────────────┐      ┌──────────────────────┐      ┌───────────────┐
│  Local machine   │      │      Firestore        │      │ GitHub Pages  │
│                  │      │   (cloud database)     │      │  (static site) │
│ generate_cards.py│─────▶│  collection: cards      │◀────▶│ index.html    │
│ (admin SDK,      │write │  - front, back, tags   │ read/│ style.css     │
│  full access,    │      │  - SM-2 state fields   │write │ app.js        │
│  Gemini API)     │      │                        │ (via │ (Firebase     │
│                  │      │  protected by          │ client│  client SDK) │
│                  │      │  firestore.rules       │ SDK) │               │
└─────────────────┘      └──────────────────────┘      └───────────────┘
```

- **No server code you write or deploy.** `generate_cards.py` /
  `import_kpss.py` run on your own machine, whenever you want to add cards.
  The web app is static files; all its logic (auth, reading cards, computing
  SM-2, writing results back) runs in the visitor's browser via the Firebase
  JS SDK talking directly to Firestore. GitHub Pages only serves the files —
  no code execution there. The actual backend (data store, auth) is Firebase,
  a managed service.
- **Two different credential types, do not confuse them:**
  - `serviceAccountKey.json` (admin, used only by the local Python script) —
    full read/write to Firestore, bypasses all security rules. Never goes in
    the repo, never goes in the browser. Listed in `.gitignore`.
  - `firebaseConfig` object (used by the browser app) — NOT a secret. It's
    fine that it's public in the deployed JS. Firestore access is actually
    controlled by `firestore.rules` (see §4), not by hiding this object.

## 2. Repo layout

```
flashcards-app/
├── generate_cards.py       ✅ done — notes → Gemini → cards (--type + --topic required)
├── import_kpss.py          ✅ done — ready Q&A (ÖSYM PDF / URL / file) → verbatim cards
├── check_firestore.py      ✅ done — one-off connectivity test (write/read/delete)
├── .env.example            ✅ done — copy to .env, holds GEMINI_API_KEY
├── requirements.txt        ✅ done
├── .gitignore               ✅ done (excludes .env, serviceAccountKey.json, notes/, *.pdf)
├── .github/workflows/pages.yml ✅ done — deploys web/ to GitHub Pages
├── firestore.rules          ✅ done — real UID pasted in & published (see §4)
├── README.md                ✅ done — step-by-step setup instructions
└── web/
    ├── index.html            ✅ done — HTML shell, 5 screens (see §5)
    ├── firebase-config.js    ✅ done — real project config (public, safe to commit)
    ├── style.css             ✅ done
    └── app.js                ✅ done — all runtime logic, see §6 for full spec
```

Not committed (gitignored): `.env` (holds `GEMINI_API_KEY`),
`serviceAccountKey.json` (admin credential), `notes/` and any `*.pdf`
(downloaded exam material — copyright).

`?demo` in the URL bypasses Firebase entirely and runs on in-memory sample
cards (both types, several topics) — used to preview the UI / SM-2 flow.

## 3. Firestore schema

Single collection: `cards`. One document per card, auto-generated ID.

```ts
{
  front: string,          // question
  back: string,           // answer
  type: "yazilim" | "kpss",  // required — chosen on the CLI, filtered in step 1
  topic: string,          // required — free-text konu, e.g. "React", "Anayasa"
  tags: string[],         // extra model-suggested tags + [type, topic] for search
  source: string,         // filename the card was generated from
  createdAt: Timestamp,

  // SM-2 spaced repetition state — mutated by the web app on every review
  repetitions: number,     // consecutive correct reviews, starts at 0
  easeFactor: number,      // starts at 2.5, clamped to >= 1.3
  interval: number,        // days until next review, starts at 0
  nextReview: Timestamp,   // when this card is next due; starts at "now"
  lastReviewed: Timestamp | null,
}
```

`generate_cards.py` already writes fresh cards in exactly this shape
(`repetitions: 0, easeFactor: 2.5, interval: 0, nextReview: now`), so every
new card is immediately due for its first review.

**Query strategy — deliberately no composite index.** Step 1 fires a single
`where("type", "==", <type>)` query (auto-indexed single field) and keeps all
of that type's cards in memory. Step 2 (topic list + counts) and step 3 (due
queue, `nextReview <= now`, sorted) are both computed client-side from that
array. Grading mutates the in-memory card object too, so topic counts stay
fresh without re-querying. If the card count per type ever gets large enough
that pulling them all is wasteful, switch to a `type + topic + nextReview`
composite index and query per topic instead.

## 4. Security model — what's left to configure (not code, Firebase console)

1. Create a Firebase project (console.firebase.google.com).
2. Enable **Firestore** (production mode, not test mode).
3. Enable **Authentication > Email/Password** provider.
4. Manually create exactly one user (your email + a password) in the
   Authentication tab. Copy its **UID**.
5. Paste that UID into `firestore.rules` in place of
   `PASTE_YOUR_UID_HERE`, then publish the rules (Firestore > Rules tab,
   paste, Publish).
6. Project settings > General > Your apps > add a Web app > copy the
   config object values into `web/firebase-config.js`.
7. Project settings > Service accounts > Generate new private key > save
   as `serviceAccountKey.json` next to `generate_cards.py` (never commit).

Net effect: the deployed site and its Firebase config are public, but
Firestore rejects every read/write that isn't authenticated as that one
UID. The generator script ignores rules entirely (admin credentials).

## 5. What index.html provides (done)

Five `<section class="screen">` blocks, toggled via a `hidden` class. `app.js`
controls which one is visible.

`style.css` and `app.js` are loaded with a `?v=N` query for cache-busting —
**bump both when you change either file**, otherwise browsers (and GitHub
Pages' CDN) serve the stale version.

Multiple-choice KPSS cards store their options in `front` newline-separated.
`app.js` `tidyFront()` normalises whitespace and, when it sees ≥3 `X)`
options, forces one option per line; `renderCard()` then adds a `.choices`
class to `#card` so `style.css` left-aligns + `white-space: pre-wrap`s the
text (plain cards stay centered serif). `collapseWs()` keeps the `back`
("Doğru cevap: X) …") on one line.

- `#signin-screen` — email/password form (`#signin-form`, `#email`,
  `#password`, `#signin-error` for error text).
- `#type-screen` — two buttons `[data-type="yazilim"]` / `[data-type="kpss"]`
  inside `#type-list`. Step 1.
- `#topic-screen` — `#topic-type-label` (which type you're in), `#topic-list`
  (populated at runtime with one `<button data-topic="...">` per distinct
  topic, each showing a due-count badge), and `#topic-back` to return to
  step 1. Step 2.
- `#study-screen` — `#deck-name` shows `Tür · Konu`; `#due-count` and a
  `#progress` / `#progress-bar` in the topbar; `#study-back` returns to the
  topic list; `#card` with `.card-front`/`.card-back` faces (`#front-text`,
  `#back-text`) inside a `.deck` wrapper (stacked-card effect, `.last` class
  when 1 card remains); `#reveal-btn`; `#grade-buttons` (hidden until
  revealed) with 4 buttons `data-grade="0|3|4|5"` (Again/Hard/Good/Easy) and
  `#good-interval` / `#easy-interval` spans for the interval preview. Step 3.
- `#empty-screen` — shown when the chosen topic has no due cards; its
  `#empty-back` button returns to the topic list.

## 6. app.js — functional spec (done — this describes the implementation)

Imports Firebase via CDN ES modules (v10 modular SDK): `initializeApp`;
`getAuth` / `signInWithEmailAndPassword` / `onAuthStateChanged`; `getFirestore`
/ `collection` / `query` / `where` / `getDocs` / `doc` / `updateDoc` /
`Timestamp`; plus `firebaseConfig` from `./firebase-config.js`. No `orderBy` /
`limit` — sorting and the due filter are done client-side (see §3).

All session state lives in one `state` object:
`{ cardsByType: {yazilim:[], kpss:[]}, type, topic, queue, index, loaded }`.

### 6.1 Boot
- Init Firebase app, auth, Firestore (skipped entirely in `?demo`).
- `onAuthStateChanged`: if signed in → `start()`; if not → sign-in screen.
  In `?demo`, call `start()` directly.
- `start()` runs `loadAllCards()` once (fills `state.cardsByType` — one
  `where("type","==",t)` getDocs per type, in parallel; in `?demo` from the
  in-memory sample set), then shows `#type-screen`.

### 6.2 Sign-in
- `#signin-form` submit handler → `signInWithEmailAndPassword(auth, email, password)`.
- On failure, show a plain-language message in `#signin-error` (e.g. "E-posta
  veya şifre yanlış." — do not leak Firebase's raw error string).

### 6.3 Step 1 → 2 → 3 navigation

**Step 1 — pick type.** `#type-screen` shows both type buttons with a due
count filled from `state.cardsByType` (0-card types dimmed via `.is-empty`).
Clicking sets `state.type` and calls the step-2 renderer.

**Step 2 — pick topic.** From `state.cardsByType[state.type]`, group by
`topic`; for each, count total and how many are due (`millis(nextReview) <=
Date.now()`, where `millis()` accepts a Timestamp, Date or number). Render
`#topic-list` sorted by topic name (`localeCompare(…, "tr")`), each button
showing `${due} kart hazır · ${total} toplam`; 0-due topics get `.is-done`
(dimmed + ✓). No cards at all → show `#topic-empty`. `#topic-back` → step 1.

**Step 3 — study.** Filter `state.cardsByType[state.type]` to the chosen
topic AND due, sort by `nextReview` ascending → `state.queue`, `index = 0`.
Empty → `#empty-screen`. Otherwise `#study-screen`: set `#deck-name` to
`${TYPE_LABELS[type]} · ${topic}`, advance the index client-side, never
re-fetch. `#study-back` / `#empty-back` → step 2 (counts recompute from the
now-mutated `state.cardsByType`, see §6.5).
- Update `#due-count` (`${remaining} kart`) and `#progress-bar` width on each
  grade; `.deck` gets `.last` when 1 card remains.

### 6.4 Showing a card
- Fill `#front-text` with `card.front`. Keep `.card-back` hidden,
  `#grade-buttons` hidden, `#reveal-btn` visible.
- `#reveal-btn` click → fill `#back-text` with `card.back`, unhide
  `.card-back`, hide `#reveal-btn`, unhide `#grade-buttons`.
- Optional nice-to-have: before revealing, compute and preview what the
  resulting interval would be for "Good" and "Easy" grades and put them in
  `#good-interval` / `#easy-interval` (e.g. "3g", "6g") — purely informational,
  computed with the same SM-2 function from §6.5 without persisting it.

### 6.5 Grading — SM-2 algorithm (implement exactly this)

```js
function sm2(card, grade) {
  // grade: 0 = Again, 3 = Hard, 4 = Good, 5 = Easy
  let { repetitions, easeFactor, interval } = card;

  if (grade < 3) {
    repetitions = 0;
    interval = 1;
  } else {
    if (repetitions === 0) interval = 1;
    else if (repetitions === 1) interval = 6;
    else interval = Math.round(interval * easeFactor);
    repetitions += 1;
  }

  easeFactor = easeFactor + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02));
  if (easeFactor < 1.3) easeFactor = 1.3;

  const nextReview = new Date();
  nextReview.setDate(nextReview.getDate() + interval);

  return { repetitions, easeFactor, interval, nextReview, lastReviewed: new Date() };
}
```

- Grade button click → call `sm2(currentCard, grade)` → `updateDoc(doc(db,
  "cards", currentCard.id), result)` → **also write the new fields back onto
  the in-memory card object** (so the topic-list due-counts in §6.3 step 2
  are correct when you go back) → advance to the next card in the in-memory
  queue (don't wait for the write to resolve before moving on; fire-and-forget
  is fine for a single-user tool, but do log/catch errors). In `?demo` skip
  the `updateDoc` but still mutate the local object.
- "Again" (grade 0) is documented in the UI as "<10dk" but this basic SM-2
  doesn't actually implement sub-day learning steps — it just resets to a
  1-day interval like a real card would after failing tomorrow. If you want
  true same-day relearning steps (Anki-style), that's a deliberate future
  upgrade, not a bug — flag it, don't silently change the label.

### 6.6 Error handling
- Firestore calls are wrapped in try/catch; on failure a `#toast` element
  (created lazily by `app.js`, styled in `style.css`) shows a short inline
  message instead of a blank screen.

## 7. style.css — done (original design direction below)

Implemented: dark radial "study room" ground, warm paper card as the hero,
`.deck` stacked-card effect behind it, Newsreader serif for card text +
system sans for chrome, single reveal transition, amber accent, red-brown
"Tekrar", `#progress-bar`, `prefers-reduced-motion` + narrow-viewport rules.
The picker screens (type/topic) reuse the same tokens.

- Background: deep ink `#1B2430`
- Card surface: warm paper `#FAF6EE`
- Accent (reveal button, "Good"/"Easy" grades): muted amber `#C98A3D`
- "Again" grade: muted red-brown, not a saturated alarm red
- Card text: serif stack (e.g. `Georgia, "Source Serif Pro", serif`) for
  front/back — readability for study content
- UI chrome (buttons, topbar): system sans stack
- Avoid: cream `#F4F1EA` + terracotta `#D97757` pairing, rounded-card SaaS
  kit with identical shadows everywhere, all-caps eyebrow labels — these
  read as generic/templated.
- Motion: a single flip/reveal transition on `.card-back` unhide is enough;
  don't add hover animations to every element.
- Must work down to a narrow mobile viewport — this will very likely be
  used on a phone.

## 8. README.md — done

Walks through, in order: local run → adding cards → from-scratch Firebase
setup → GitHub Pages deploy → the 3-step study flow. Also documents `?demo`.

**Deploy**: `.github/workflows/pages.yml` uploads `web/` as the Pages
artifact (`upload-pages-artifact` → `deploy-pages`), triggered on push to
`main` touching `web/**`, or manually. Repo Settings > Pages > Source must be
set to **GitHub Actions** (one time). This sidesteps the "Deploy from a
branch" limitation (that mode only serves repo root or `/docs`, never
`/web`). Published at `https://<user>.github.io/restudy/` — the app uses only
relative asset paths + CDN imports, so the `/restudy/` subpath is fine.
**Firebase Auth → Settings → Authorized domains must include
`<user>.github.io`** or sign-in fails on the live site.

## 9. Known open questions / things to decide before or during implementation

- **Gemini model**: `generate_cards.py` and `import_kpss.py` default to
  `gemini-3.1-flash-lite` (env override: `GEMINI_MODEL`, e.g.
  `gemini-3.1-pro-preview` for harder parsing). Confirm the id still resolves
  when you run it — Google rotates these. Also: the pinned `google-generativeai`
  package is deprecated in favour of `google-genai`; still runs, migration on
  the horizon.
- **KPSS content pipeline — built, `import_kpss.py`.** Two ingestion paths now
  exist:
  - **Notes** (`generate_cards.py --type kpss --topic "Tarih" notes/…`) —
    Gemini *writes* cards from your prose. Good for topic coverage / weak
    areas.
  - **Ready Q&A** (`import_kpss.py <pdf|url|file> --topic "Tarih"`) — Gemini
    only *extracts* questions verbatim (own prompt: no paraphrase, no
    inventing, skip image-only / garbled questions), dedups by normalized
    `front` against existing kpss cards for that topic, `--dry-run` to
    preview, `⚠ ŞÜPHELİ` cards skipped unless `--keep-suspect`. Handles BOTH
    formats: multiple-choice (stem + A–E + marked answer → `front` keeps the
    options, `back` = "Doğru cevap: X)…") and open Q&A ("Soru: … / Cevap: …"
    → `front` = stem, `back` = answer). Sources: ÖSYM PDF (pymupdf
    `sort=True`), a URL (requests + BeautifulSoup text), or a local
    `.html`/`.txt`/`.md`. **`--vision`** uploads a local PDF/PNG/JPG straight
    to Gemini (`genai.upload_file`) — OCR + extraction in one call; on the
    ÖSYM %10 booklet it gave 5 flawless cards where text mode gave 8 with 2
    garbled. Costs image tokens, slower; use it for image-heavy / scanned.
    `--auto-topic` asks Gemini to also classify each question's subject
    (Tarih/Coğrafya/Türkçe/Matematik/Vatandaşlık/Güncel Bilgiler) and writes
    each card to that topic — turns a mixed GY-GK booklet into per-subject
    decks. `_parse_cards` also strips leading "Soru 12)" / "12." numbering
    and `looks_broken` now rejects vague recall stubs ("… sorusu").
  - **Current KPSS deck** (2026-09): ~106 cards — Tarih 57 (tarihvakti.com
    recall pages 2021-2024, plain-text blog, clean), Coğrafya 38 (a mixed
    2024 GY-GK PDF the user supplied, `--vision`), the rest (~11) from ÖSYM
    %10 booklets via `--vision --auto-topic`. Türkçe/Vatandaşlık/Matematik
    are still thin — need more sources.
  - **Reality of sources** (checked 2026-09): ÖSYM only publishes ~10% of
    each exam (the "%10'luk kitapçık" — ~12 Q; text mode: GK OK, GY math is
    images; `--vision` recovers most). No clean plain-text blog exists for
    Coğrafya / Vatandaşlık like tarihvakti does for Tarih. Online quiz sites
    (kpsscini, onlinesoru, …) reveal answers via JS → `requests` won't see
    them; save the page (or screenshot + `--vision`). Pirate doc hosts
    (Scribd, dokumen.pub) and publisher PDFs (Pegem, Yargı) are off-limits —
    commercial copyrighted banks, not public exam material. Blog-style recall
    pages that print `Soru/Cevap` inline as plain text (e.g. tarihvakti.com)
    extract cleanly (~15-20 cards/page), but the wording is reconstructed by
    test-takers, not official — expect the odd vague stub. Further volume
    needs the user's own legally-held files or manually saved pages. Target
    level is **Ön Lisans** (≈ Ortaöğretim; same subjects/weights, easier than
    Lisans — either level's material works).
    ToS/copyright of third-party sites is the user's call; keep the card DB
    private, don't commit question content (`notes/`, `*.pdf` are gitignored).
- **Duplicate cards on re-runs**: `generate_cards.py` currently has no
  dedup — running it twice on the same note creates duplicate cards. Not
  handled yet; either dedupe by `(type, topic, source, front)` before
  writing, or treat this as a "don't re-run on unchanged notes" manual
  discipline for now.
- **Topics are free text**: `--topic "React"` and `--topic "react"` become
  two separate topics in the step-2 list. No normalization / autocomplete
  yet — just be consistent when generating.
- **"Again" sub-day steps**: see §6.5 — flagged as a possible future
  upgrade, not implemented in the base SM-2.
- **Card ordering within a session**: step 3 sorts by `nextReview` ascending
  client-side; no shuffling. Fine for a single-user tool, mention if a more
  Anki-like new/review mix is wanted later.
