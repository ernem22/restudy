// app.js — tüm çalışma zamanı mantığı tarayıcıda döner.
// Firebase v10 modüler SDK, CDN üzerinden ES modülleri olarak.
//
// Akış: giriş → 1) tür seç → 2) konu seç → 3) o konunun hazır kartlarını çalış.
// Demo modu: URL'ye ?demo eklersen Firebase baypas edilir, sahte kartlarla
// çalışır (kalıcılık yok). Tasarımı ve SM-2 akışını görmek için.

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth,
  signInWithEmailAndPassword,
  onAuthStateChanged,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import {
  getFirestore,
  collection,
  query,
  where,
  getDocs,
  doc,
  updateDoc,
  Timestamp,
  serverTimestamp,
  deleteField,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { firebaseConfig } from "./firebase-config.js";

const DEMO = new URLSearchParams(location.search).has("demo");

// Alanlar: sources.json'daki `group` değerleri. Firestore'da kartın `type` alanı bu kimliği taşır.
// "yazilim" eski (Gemini ile üretilmiş) kartlar için; kart yoksa ekranda görünmez.
const GROUPS = [
  { id: "frontend", label: "Frontend" },
  { id: "backend", label: "Backend" },
  { id: "db", label: "Veritabanı" },
  { id: "devops", label: "DevOps" },
  { id: "general", label: "Genel Programlama" },
  { id: "ai", label: "Yapay Zekâ" },
  { id: "yazilim", label: "Yazılım (eski)" },
];
const CARD_TYPES = GROUPS.map((g) => g.id);
const TYPE_LABELS = Object.fromEntries(GROUPS.map((g) => [g.id, g.label]));

// ---- boot -----------------------------------------------------------

let auth = null;
let db = null;

if (!DEMO) {
  const fbApp = initializeApp(firebaseConfig);
  auth = getAuth(fbApp);
  db = getFirestore(fbApp);
}

// tüm oturum durumu tek yerde
const state = {
  cardsByType: Object.fromEntries(CARD_TYPES.map((t) => [t, []])), // alana göre bellekteki kartlar
  type: null,
  topic: null,
  queue: [], // seçili konunun hazır kartları
  index: 0,
  loaded: false,
  session: { unique: new Set(), again: 0 }, // seans özeti
};

// ---- dom -----------------------------------------------------------

const screens = {
  signin: document.getElementById("signin-screen"),
  type: document.getElementById("type-screen"),
  topic: document.getElementById("topic-screen"),
  skipped: document.getElementById("skipped-screen"),
  study: document.getElementById("study-screen"),
  empty: document.getElementById("empty-screen"),
};

const signinForm = document.getElementById("signin-form");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const signinError = document.getElementById("signin-error");

const typeList = document.getElementById("type-list");

const topicList = document.getElementById("topic-list");
const topicTypeLabel = document.getElementById("topic-type-label");
const topicEmpty = document.getElementById("topic-empty");
const topicBack = document.getElementById("topic-back");

const progressBar = document.getElementById("progress-bar");
const deckEl = document.querySelector(".deck");
const stageEl = document.querySelector(".stage");
const deckName = document.getElementById("deck-name");
const dueCount = document.getElementById("due-count");
const studyBack = document.getElementById("study-back");
const cardEl = document.getElementById("card");
const frontText = document.getElementById("front-text");
const backText = document.getElementById("back-text");
const lessonOpen = document.getElementById("lesson-open");
const reportBtn = document.getElementById("report-btn");
const skipBtn = document.getElementById("skip-btn");
const skippedOpen = document.getElementById("skipped-open");
const skippedBack = document.getElementById("skipped-back");
const skippedList = document.getElementById("skipped-list");
const skippedSub = document.getElementById("skipped-sub");
const lessonScreen = document.getElementById("lesson-screen");
const lessonBody = document.getElementById("lesson-body");
const lessonLabel = document.getElementById("lesson-label");
const lessonClose = document.getElementById("lesson-close");
const lessonScroll = lessonScreen.querySelector(".lesson-scroll");
const cardBack = cardEl.querySelector(".card-back");
const revealBtn = document.getElementById("reveal-btn");
const gradeButtons = document.getElementById("grade-buttons");
const goodInterval = document.getElementById("good-interval");
const easyInterval = document.getElementById("easy-interval");
const emptyBack = document.getElementById("empty-back");
const emptyTitle = document.getElementById("empty-title");
const emptySub = document.getElementById("empty-sub");

// ---- yardımcılar --------------------------------------------------

function showScreen(name) {
  for (const [key, el] of Object.entries(screens)) {
    el.classList.toggle("hidden", key !== name);
  }
}

// nextReview alanı Firestore Timestamp, JS Date veya sayı olabilir
function millis(v) {
  if (v == null) return 0;
  if (typeof v === "number") return v;
  if (typeof v.toMillis === "function") return v.toMillis();
  if (typeof v.getTime === "function") return v.getTime();
  if (v.seconds != null) return v.seconds * 1000;
  return 0;
}

function isDue(card) {
  return millis(card.nextReview) <= Date.now();
}

// ---- öğretici kaynak (markdown okuyucu) -------------------------

function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// satır içi işaretleme: `kod`, **kalın**, *italik*, [metin](url)
function inlineMd(s) {
  let t = escapeHtml(s);
  const codes = [];
  t = t.replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0000${codes.length - 1}\u0000`;
  });
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(
    /(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g,
    "$1<em>$2</em>",
  );
  t = t.replace(
    /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>',
  );
  t = t.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
  return t;
}

// küçük, bağımsız markdown ayrıştırıcı: başlık, liste, kod bloğu, alıntı, hr
function mdToHtml(src) {
  const lines = String(src || "").split("\n");
  const out = [];
  const para = [];
  let i = 0;

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${inlineMd(para.join(" "))}</p>`);
      para.length = 0;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // ``` kod bloğu: süreç aynı kapanışa kadar
    if (/^\s*```/.test(line)) {
      flushPara();
      const body = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1;
      out.push(`<pre><code>${escapeHtml(body.join("\n"))}</code></pre>`);
      continue;
    }

    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      flushPara();
      out.push(`<h${h[1].length}>${inlineMd(h[2])}</h${h[1].length}>`);
      i += 1;
      continue;
    }

    if (/^\s*(?:-{3,}|\*{3,})\s*$/.test(line)) {
      flushPara();
      out.push("<hr>");
      i += 1;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      flushPara();
      const body = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      out.push(`<blockquote><p>${inlineMd(body.join(" "))}</p></blockquote>`);
      continue;
    }

    const ul = /^\s*[-*]\s+(.*)$/.exec(line);
    const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (ul || ol) {
      flushPara();
      const tag = ul ? "ul" : "ol";
      const items = [];
      while (i < lines.length) {
        const m = ul
          ? /^\s*[-*]\s+(.*)$/.exec(lines[i])
          : /^\s*\d+[.)]\s+(.*)$/.exec(lines[i]);
        if (!m) {
          if (!/^\s*$/.test(lines[i])) break; // liste bitti
          i += 1; // boş satırı atla
          continue;
        }
        items.push(`<li>${inlineMd(m[1])}</li>`);
        i += 1;
      }
      out.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }

    if (/^\s*$/.test(line)) {
      flushPara();
      i += 1;
      continue;
    }

    para.push(line.trim());
    i += 1;
  }
  flushPara();
  return out.join("");
}

function renderLesson(card) {
  lessonLabel.textContent = `${TYPE_LABELS[card.type]} · ${card.topic}`;
  lessonBody.innerHTML = mdToHtml(card.detail);
  lessonScroll.scrollTop = 0;
}

function openLesson() {
  const card = state.queue[state.index];
  if (!card || !card.detail) return;
  renderLesson(card);
  lessonScreen.classList.remove("hidden");
  lessonClose.focus();
}

function closeLesson() {
  lessonScreen.classList.add("hidden");
}

lessonOpen.addEventListener("click", openLesson);
lessonClose.addEventListener("click", closeLesson);

let toastEl = null;
function toast(message) {
  if (!toastEl) {
    toastEl = document.createElement("div");
    toastEl.id = "toast";
    document.body.appendChild(toastEl);
  }
  toastEl.textContent = message;
  toastEl.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => toastEl.classList.remove("show"), 3500);
}

// ---- auth --------------------------------------------------------

if (DEMO) {
  start();
} else {
  onAuthStateChanged(auth, (user) => {
    if (user) start();
    else showScreen("signin");
  });

  signinForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    signinError.textContent = "";
    const btn = signinForm.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await signInWithEmailAndPassword(
        auth,
        emailInput.value.trim(),
        passwordInput.value,
      );
      // onAuthStateChanged devralır
    } catch (err) {
      console.error("giriş hatası:", err);
      signinError.textContent = "E-posta veya şifre yanlış.";
    } finally {
      btn.disabled = false;
    }
  });
}

// ---- veri yükleme ----------------------------------------------

async function start() {
  if (!state.loaded) {
    try {
      await loadAllCards();
      state.loaded = true;
    } catch (err) {
      console.error("kartlar yüklenemedi:", err);
      toast("Kartlar yüklenemedi. Bağlantını kontrol et.");
    }
  }
  showTypeScreen();
}

async function loadAllCards() {
  if (DEMO) {
    for (const t of CARD_TYPES) {
      state.cardsByType[t] = demoCards().filter((c) => c.type === t);
    }
    return;
  }
  await Promise.all(
    CARD_TYPES.map(async (t) => {
      const q = query(collection(db, "cards"), where("type", "==", t));
      const snap = await getDocs(q);
      state.cardsByType[t] = snap.docs.map((d) => ({ id: d.id, ...d.data() }));
    }),
  );
}

// ---- 1. adım: tür ---------------------------------------------

function showTypeScreen() {
  if (!typeList.querySelector("[data-type]")) {
    for (const g of GROUPS) {
      const btn = document.createElement("button");
      btn.dataset.type = g.id;
      btn.className = "type-card";
      btn.innerHTML = '<span class="type-name"></span><span class="type-meta" data-count>—</span>';
      btn.querySelector(".type-name").textContent = g.label;
      typeList.appendChild(btn);
    }
  }
  // kartı olmayan alanlar gizlenir; hiç kart yoksa hepsi "kart yok" olarak görünür
  const any = CARD_TYPES.some((t) => (state.cardsByType[t] || []).length > 0);
  for (const btn of typeList.querySelectorAll("[data-type]")) {
    const cards = state.cardsByType[btn.dataset.type] || [];
    const due = cards.filter((c) => !c.skipped && isDue(c)).length;
    const meta = btn.querySelector("[data-count]");
    meta.textContent = cards.length === 0 ? "kart yok" : `${due} kart hazır`;
    btn.classList.toggle("is-empty", cards.length === 0);
    btn.classList.toggle("hidden", any && cards.length === 0);
  }
  showScreen("type");
}

typeList.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-type]");
  if (!btn) return;
  state.type = btn.dataset.type;
  showTopicScreen();
});

// ---- 2. adım: konu -------------------------------------------

function showTopicScreen() {
  const cards = state.cardsByType[state.type] || [];
  topicTypeLabel.textContent = TYPE_LABELS[state.type];
  topicList.innerHTML = "";
  skippedOpen.classList.add("hidden");

  if (cards.length === 0) {
    topicEmpty.classList.remove("hidden");
    showScreen("topic");
    return;
  }
  topicEmpty.classList.add("hidden");

  // konuya göre grupla, hazır sayısını hesapla
  const byTopic = new Map();
  for (const c of cards) {
    if (c.skipped) continue; // "sorma" işaretli kartlar sayıya ve kuyruğa girmez
    const key = c.topic || "(konusuz)";
    if (!byTopic.has(key)) byTopic.set(key, { total: 0, due: 0 });
    const g = byTopic.get(key);
    g.total += 1;
    if (isDue(c)) g.due += 1;
  }

  const topics = [...byTopic.entries()].sort((a, b) =>
    a[0].localeCompare(b[0], "tr"),
  );

  for (const [topic, g] of topics) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "topic-btn";
    btn.dataset.topic = topic;
    if (g.due === 0) btn.classList.add("is-done");
    btn.innerHTML =
      `<span class="topic-name"></span>` +
      `<span class="topic-meta">${g.due} kart hazır · ${g.total} toplam</span>`;
    btn.querySelector(".topic-name").textContent = topic;
    topicList.appendChild(btn);
  }

  const nSkipped = cards.filter((c) => c.skipped).length;
  skippedOpen.textContent = `Sorulmayacak sorular (${nSkipped})`;
  skippedOpen.classList.toggle("hidden", nSkipped === 0);

  showScreen("topic");
}

topicList.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-topic]");
  if (!btn) return;
  state.topic = btn.dataset.topic;
  startStudy();
});

topicBack.addEventListener("click", showTypeScreen);
studyBack.addEventListener("click", showTopicScreen);
emptyBack.addEventListener("click", showTopicScreen);

// ---- 3. adım: çalışma --------------------------------------

function startStudy() {
  const cards = state.cardsByType[state.type] || [];
  state.queue = cards
    .filter((c) => !c.skipped && (c.topic || "(konusuz)") === state.topic && isDue(c))
    .sort((a, b) => millis(a.nextReview) - millis(b.nextReview));
  state.index = 0;
  state.session = { unique: new Set(), again: 0 };

  if (state.queue.length === 0) {
    showEmpty("none");
    return;
  }

  deckName.textContent = `${TYPE_LABELS[state.type]} · ${state.topic}`;
  showScreen("study");
  renderCard();
}

function showEmpty(kind) {
  if (kind === "done") {
    const { unique, again } = state.session;
    emptyTitle.textContent = "Seans tamamlandı.";
    emptySub.textContent =
      `${unique.size} kart çalıştın.` +
      (again ? ` ${again} kez tekrar ettin.` : "");
  } else {
    emptyTitle.textContent = "Bu konuda hazır kart yok.";
    emptySub.textContent = "Sırada bekleyen kart kalmadı.";
  }
  showScreen("empty");
}

function remaining() {
  return state.queue.length - state.index;
}

// fazla boşlukları sadeleştir, satır sonlarını koru
function collapseWs(s) {
  return String(s || "").replace(/[ \t]+/g, " ").replace(/ *\n */g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}
// çoktan seçmeli soru: en az 3 "X)" şıkkı varsa her şıkkı ayrı satıra al
function tidyFront(s) {
  s = collapseWs(s);
  if ((s.match(/(?:^|\s)[A-E]\) /g) || []).length < 3) return s;
  return s.replace(/ (?=[A-E]\) )/g, "\n").replace(/\n(?=[A-E]\) )/g, "\n").trim();
}
function hasChoices(s) {
  return /\n[A-E]\) /.test(s);
}

// çoktan seçmelide kök ve her şık ayrı blok; şık kayınca devamı kendi altında kalır
function renderFront(text) {
  if (!hasChoices(text)) {
    frontText.textContent = text;
    return;
  }
  const stem = [];
  const options = [];
  for (const line of text.split("\n")) {
    if (/^[A-E]\) /.test(line)) options.push(line);
    else if (options.length) options[options.length - 1] += " " + line;
    else stem.push(line);
  }
  frontText.textContent = "";
  if (stem.length) {
    const el = document.createElement("span");
    el.className = "q-stem";
    el.textContent = stem.join(" ");
    frontText.appendChild(el);
  }
  for (const line of options) {
    const el = document.createElement("span");
    el.className = "q-opt";
    el.textContent = line;
    frontText.appendChild(el);
  }
}

function renderCard() {
  const card = state.queue[state.index];
  if (!card) {
    showEmpty("done");
    return;
  }

  const front = tidyFront(card.front);
  cardEl.classList.toggle("choices", hasChoices(front));
  renderFront(front);
  backText.textContent = "";
  lessonOpen.classList.add("hidden");
  closeLesson();
  cardBack.classList.add("hidden");
  cardEl.classList.remove("revealed");
  gradeButtons.classList.add("hidden");
  revealBtn.classList.remove("hidden");
  stageEl.scrollTop = 0;

  dueCount.textContent = `${remaining()} kart`;
  progressBar.style.width = `${(state.index / state.queue.length) * 100}%`;
  deckEl.classList.toggle("last", remaining() <= 1);

  // bilgi amaçlı aralık önizlemesi (kalıcı değil)
  goodInterval.textContent = formatInterval(sm2(card, 4).interval);
  easyInterval.textContent = formatInterval(sm2(card, 5).interval);
}

function formatInterval(days) {
  return `${days}g`;
}

revealBtn.addEventListener("click", () => {
  const card = state.queue[state.index];
  if (!card) return;
  backText.textContent = collapseWs(card.back);
  if (card.detail && card.detail.trim()) lessonOpen.classList.remove("hidden");
  renderReport(card);
  cardBack.classList.remove("hidden");
  cardEl.classList.add("revealed");
  revealBtn.classList.add("hidden");
  gradeButtons.classList.remove("hidden");

  // uzun kartta cevap ekranın altında kalmasın
  requestAnimationFrame(() => {
    const stageRect = stageEl.getBoundingClientRect();
    const backRect = cardBack.getBoundingClientRect();
    if (backRect.top <= stageRect.bottom - 80) return;
    stageEl.scrollBy({
      top: backRect.top - stageRect.top - stageRect.height * 0.2,
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
  });
});

// ---- SM-2 ---------------------------------------------------

function sm2(card, grade) {
  // grade: 0 = Tekrar, 3 = Zor, 4 = İyi, 5 = Kolay
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

  return {
    repetitions,
    easeFactor,
    interval,
    nextReview,
    lastReviewed: new Date(),
  };
}

// ---- hatalı kart bildirimi -------------------------------------
// Firestore'da kartın `reported` (bool) ve `reportedAt` (zaman) alanları. Kart silinmez/gizlenmez;
// bildirilenler `python reported_cards.py` ile listelenip düzeltilir.

function renderReport(card) {
  const on = Boolean(card.reported);
  reportBtn.classList.toggle("is-reported", on);
  reportBtn.setAttribute("aria-pressed", String(on));
  reportBtn.textContent = on ? "⚑ Bildirildi · geri al" : "⚑ Hatalı bildir";
}

reportBtn.addEventListener("click", () => {
  const card = state.queue[state.index];
  if (!card) return;
  const next = !card.reported;
  card.reported = next;
  renderReport(card);
  if (DEMO) return;
  updateDoc(
    doc(db, "cards", card.id),
    next ? { reported: true, reportedAt: serverTimestamp() } : { reported: false, reportedAt: deleteField() },
  ).catch((err) => {
    console.error("bildirim kaydedilemedi:", err);
    card.reported = !next; // geri al
    renderReport(card);
    toast("Bildirim kaydedilemedi.");
  });
  toast(next ? "Hatalı olarak işaretlendi." : "İşaret kaldırıldı.");
});

// ---- "bu soruyu sorma" ------------------------------------------
// Firestore'da kartın `skipped` (bool) ve `skippedAt` alanı. Kart silinmez; çalışma kuyruğundan ve
// konu sayılarından çıkar. "Sorulmayacak sorular" ekranından geri alınır.

function saveSkipped(card, value) {
  if (DEMO) return Promise.resolve();
  return updateDoc(
    doc(db, "cards", card.id),
    value ? { skipped: true, skippedAt: serverTimestamp() } : { skipped: false, skippedAt: deleteField() },
  );
}

skipBtn.addEventListener("click", () => {
  const card = state.queue[state.index];
  if (!card) return;
  card.skipped = true;
  state.queue.splice(state.index, 1); // sıradaki kart aynı indekse kayar; notlama yapılmaz
  saveSkipped(card, true).catch((err) => {
    console.error("işaret kaydedilemedi:", err);
    card.skipped = false;
    toast("Kaydedilemedi; soru sonra tekrar gelecek.");
  });
  toast("Soru gizlendi. Konu ekranından geri alabilirsin.");
  if (state.index >= state.queue.length) {
    showEmpty("done");
    return;
  }
  renderCard();
});

function showSkippedScreen() {
  const cards = (state.cardsByType[state.type] || [])
    .filter((c) => c.skipped)
    .sort((a, b) => (a.topic || "").localeCompare(b.topic || "", "tr") || String(a.front).localeCompare(String(b.front), "tr"));
  skippedSub.textContent = `${TYPE_LABELS[state.type]} · ${cards.length} soru`;
  skippedList.innerHTML = "";
  for (const c of cards) {
    const row = document.createElement("div");
    row.className = "skipped-item";
    row.innerHTML =
      '<div class="skipped-text"><p class="skipped-q"></p><p class="skipped-meta"></p></div>' +
      '<button type="button" class="skipped-restore">Geri al</button>';
    row.querySelector(".skipped-q").textContent = collapseWs(tidyFront(c.front));
    row.querySelector(".skipped-meta").textContent = c.topic || "(konusuz)";
    row.querySelector(".skipped-restore").dataset.id = c.id;
    skippedList.appendChild(row);
  }
  showScreen("skipped");
}

skippedList.addEventListener("click", (e) => {
  const btn = e.target.closest(".skipped-restore");
  if (!btn) return;
  const card = (state.cardsByType[state.type] || []).find((c) => c.id === btn.dataset.id);
  if (!card) return;
  card.skipped = false;
  saveSkipped(card, false).catch((err) => {
    console.error("işaret kaldırılamadı:", err);
    card.skipped = true;
    toast("Kaydedilemedi.");
    showSkippedScreen();
  });
  toast("Soru geri alındı.");
  if ((state.cardsByType[state.type] || []).some((c) => c.skipped)) showSkippedScreen();
  else showTopicScreen();
});

skippedOpen.addEventListener("click", showSkippedScreen);
skippedBack.addEventListener("click", showTopicScreen);

// ---- notlama ----------------------------------------------

gradeButtons.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-grade]");
  if (!btn) return;
  const grade = Number(btn.dataset.grade);
  const card = state.queue[state.index];
  if (!card) return;

  const result = sm2(card, grade);
  state.session.unique.add(card.id || card.front);
  if (grade < 3) state.session.again += 1;

  // bellekteki kart nesnesini de güncelle — geri dönünce konu sayıları doğru olsun
  card.repetitions = result.repetitions;
  card.easeFactor = result.easeFactor;
  card.interval = result.interval;
  card.nextReview = result.nextReview;
  card.lastReviewed = result.lastReviewed;

  if (!DEMO) {
    // fire-and-forget yazma; tek kullanıcılık araç için yeterli, ama hatayı yakala
    updateDoc(doc(db, "cards", card.id), {
      repetitions: result.repetitions,
      easeFactor: result.easeFactor,
      interval: result.interval,
      nextReview: Timestamp.fromDate(result.nextReview),
      lastReviewed: Timestamp.fromDate(result.lastReviewed),
    }).catch((err) => {
      console.error("kart güncellenemedi:", err);
      toast("Sonuç kaydedilemedi.");
    });
  }

  state.index += 1;
  // "Tekrar" kartı aynı seansın sonuna ekle — bugün tekrar gelsin
  if (grade < 3) state.queue.push(card);
  closeLesson();
  if (remaining() <= 0) {
    progressBar.style.width = "100%";
    showEmpty("done");
  } else {
    renderCard();
  }
});

// klavye kısayolları: boşluk = göster, 1-4 = notla
document.addEventListener("keydown", (e) => {
  if (!lessonScreen.classList.contains("hidden")) {
    if (e.code === "Escape" || e.code === "Enter") {
      e.preventDefault();
      closeLesson();
    }
    return;
  }
  if (screens.study.classList.contains("hidden")) return;
  if (!revealBtn.classList.contains("hidden")) {
    if (e.code === "Space" || e.code === "Enter") {
      e.preventDefault();
      revealBtn.click();
    }
    return;
  }
  const map = { Digit1: 0, Digit2: 3, Digit3: 4, Digit4: 5 };
  if (e.code in map) {
    const target = gradeButtons.querySelector(
      `button[data-grade="${map[e.code]}"]`,
    );
    if (target) target.click();
  }
});

// ---- demo verisi ----------------------------------------

function demoCards() {
  const base = { repetitions: 0, easeFactor: 2.5, interval: 0 };
  const mk = (type, topic, front, back, detail = "") => ({
    id: `${type}-${topic}-${front}`.slice(0, 40),
    type,
    topic,
    front,
    back,
    detail,
    tags: [type, topic],
    nextReview: new Date(Date.now() - 1000),
    ...base,
  });
  return [
    mk("general", "SM-2", "SM-2'de easeFactor'ün alt sınırı kaçtır?", "1.3"),
    mk("general", "SM-2", "Bir kartı ilk kez doğru bildiğinde interval kaç gün olur?", "1 gün"),
    mk("general", "SM-2", "repetitions = 1 iken doğru cevapta interval kaça çıkar?", "6 gün"),
    mk("general", "SM-2", '"Tekrar" (grade 0) verince repetitions ne olur?', "0'a sıfırlanır, interval 1 güne düşer."),
    mk("frontend", "React", "useEffect'in bağımlılık dizisi boşsa etki ne zaman çalışır?", "Sadece ilk render'dan sonra bir kez."),
    mk("frontend", "React", "React'te key prop ne işe yarar?", "Liste elemanlarını render'lar arası eşleştirip gereksiz yeniden oluşturmayı önler.",
      "React varsayılan olarak elemanları sıraya göre eşleştirir; listenin başına eleman eklenince tüm alt ağaç gereksiz güncellenir. Stabil bir key (veritabanı id'si) verirsen React elemanları taşır, sadece değişen yer güncellenir."),
    mk(
      "frontend",
      "React",
      "useEffect ne zaman ve nasıl çalışır?",
      "Render tamamlandıktan sonra; bağımlılık dizisindeki değerler her değiştiğinde.",
      `## useEffect nedir

useEffect, React'in **yan etki (side effect)** kancasıdır. Komponentin render olduktan *sonra* yapması gereken işleri tanımlar: veri çekme, abonelik, DOM ölçümü, zamanlayıcılar...

### Ne zaman çalışır?

1. İlk mount sonrası — mutlaka bir kez
2. Bağımlılık dizisindeki değerlerden biri değiştikten sonra
3. Bir sonraki efektden önce ve unmount'ta temizlik (cleanup) çalışır

## Temel örnek

\`\`\`js
useEffect(() => {
  const id = setInterval(tik, 1000);
  return () => clearInterval(id); // temizlik
}, []); // boş dizi: sadece ilk montajda çalışır
\`\`\`

## Sık tuzağlar

- **Eksik bağımlılık**: diziye koymadığın değer "bayat" (stale) kalır.
- **Nesne/fonksiyon bağımlılığı**: her render'da yeni referans yaratır, efekt gereksiz çalışır — useMemo ile sabitle.
- **Cleanup atlama**: return edilen temizliği yazmayınca abonelikler sızar.

> Kural: efect içinde kullandığın her render zamanlı değeri bağımlılık dizisine yaz; gerçekten sadece bir kez okumak istiyorsan useRef ile sabitle.

---

Ayrıntılar için [React dokümantasyonu](https://react.dev/reference/react/useEffect) konusuna bakabilirsin.
`,
    ),
  ];
}
