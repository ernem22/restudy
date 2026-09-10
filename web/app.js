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
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { firebaseConfig } from "./firebase-config.js";

const DEMO = new URLSearchParams(location.search).has("demo");

const CARD_TYPES = ["yazilim", "kpss"];
const TYPE_LABELS = { yazilim: "Yazılım", kpss: "KPSS" };

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
  cardsByType: { yazilim: [], kpss: [] }, // türe göre bellekteki kartlar
  type: null,
  topic: null,
  queue: [], // seçili konunun hazır kartları
  index: 0,
  loaded: false,
};

// ---- dom -----------------------------------------------------------

const screens = {
  signin: document.getElementById("signin-screen"),
  type: document.getElementById("type-screen"),
  topic: document.getElementById("topic-screen"),
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
const deckName = document.getElementById("deck-name");
const dueCount = document.getElementById("due-count");
const studyBack = document.getElementById("study-back");
const cardEl = document.getElementById("card");
const frontText = document.getElementById("front-text");
const backText = document.getElementById("back-text");
const cardBack = cardEl.querySelector(".card-back");
const revealBtn = document.getElementById("reveal-btn");
const gradeButtons = document.getElementById("grade-buttons");
const goodInterval = document.getElementById("good-interval");
const easyInterval = document.getElementById("easy-interval");
const emptyBack = document.getElementById("empty-back");

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
  for (const btn of typeList.querySelectorAll("[data-type]")) {
    const cards = state.cardsByType[btn.dataset.type] || [];
    const due = cards.filter(isDue).length;
    const meta = btn.querySelector("[data-count]");
    meta.textContent = cards.length === 0 ? "kart yok" : `${due} kart hazır`;
    btn.classList.toggle("is-empty", cards.length === 0);
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

  if (cards.length === 0) {
    topicEmpty.classList.remove("hidden");
    showScreen("topic");
    return;
  }
  topicEmpty.classList.add("hidden");

  // konuya göre grupla, hazır sayısını hesapla
  const byTopic = new Map();
  for (const c of cards) {
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
    .filter((c) => (c.topic || "(konusuz)") === state.topic && isDue(c))
    .sort((a, b) => millis(a.nextReview) - millis(b.nextReview));
  state.index = 0;

  if (state.queue.length === 0) {
    showScreen("empty");
    return;
  }

  deckName.textContent = `${TYPE_LABELS[state.type]} · ${state.topic}`;
  showScreen("study");
  renderCard();
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

function renderCard() {
  const card = state.queue[state.index];
  if (!card) {
    showScreen("empty");
    return;
  }

  const front = tidyFront(card.front);
  cardEl.classList.toggle("choices", hasChoices(front));
  frontText.textContent = front;
  backText.textContent = "";
  cardBack.classList.add("hidden");
  cardEl.classList.remove("revealed");
  gradeButtons.classList.add("hidden");
  revealBtn.classList.remove("hidden");

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
  cardBack.classList.remove("hidden");
  cardEl.classList.add("revealed");
  revealBtn.classList.add("hidden");
  gradeButtons.classList.remove("hidden");
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

// ---- notlama ----------------------------------------------

gradeButtons.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-grade]");
  if (!btn) return;
  const grade = Number(btn.dataset.grade);
  const card = state.queue[state.index];
  if (!card) return;

  const result = sm2(card, grade);

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
  if (remaining() <= 0) {
    progressBar.style.width = "100%";
    showScreen("empty");
  } else {
    renderCard();
  }
});

// klavye kısayolları: boşluk = göster, 1-4 = notla
document.addEventListener("keydown", (e) => {
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
  const mk = (type, topic, front, back) => ({
    id: `${type}-${topic}-${front}`.slice(0, 40),
    type,
    topic,
    front,
    back,
    tags: [type, topic],
    nextReview: new Date(Date.now() - 1000),
    ...base,
  });
  return [
    mk("yazilim", "SM-2", "SM-2'de easeFactor'ün alt sınırı kaçtır?", "1.3"),
    mk("yazilim", "SM-2", "Bir kartı ilk kez doğru bildiğinde interval kaç gün olur?", "1 gün"),
    mk("yazilim", "SM-2", "repetitions = 1 iken doğru cevapta interval kaça çıkar?", "6 gün"),
    mk("yazilim", "SM-2", '"Tekrar" (grade 0) verince repetitions ne olur?', "0'a sıfırlanır, interval 1 güne düşer."),
    mk("yazilim", "React", "useEffect'in bağımlılık dizisi boşsa etki ne zaman çalışır?", "Sadece ilk render'dan sonra bir kez."),
    mk("yazilim", "React", "React'te key prop ne işe yarar?", "Liste elemanlarını render'lar arası eşleştirip gereksiz yeniden oluşturmayı önler."),
    mk("kpss", "Anayasa Hukuku", "1982 Anayasası'na göre egemenlik kime aittir?", "Kayıtsız şartsız millete."),
    mk("kpss", "Anayasa Hukuku", "TBMM üye tam sayısı kaçtır?", "600"),
    mk("kpss", "Anayasa Hukuku", "Anayasa Mahkemesi kaç üyeden oluşur?", "15"),
    mk("kpss", "Coğrafya", "Türkiye'nin en yüksek dağı hangisidir?", "Ağrı Dağı (5.137 m)."),
    mk(
      "kpss",
      "Coğrafya",
      "Türkiye'de güneyden kuzeye gidildikçe aşağıdakilerden hangisi görülmez?\nA) Gölge boyunun uzaması\nB) Güneş ışınlarının düşme açısının azalması\nC) Çizgisel hızın azalması\nD) Denizlerin tuzluluğunun azalması\nE) Yağışların azalması",
      "Doğru cevap: E) Yağışların azalması",
    ),
  ];
}
