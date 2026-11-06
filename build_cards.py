"""Docs -> Turkish flashcards with a lesson per section. Generation runs through the
`opencode` CLI (free models). One-shot, resumable per section.

    python build_cards.py fetch    --topic html
    python build_cards.py split    --topic html
    python build_cards.py generate --topic html --model opencode/glm-5.3-flash-free --limit 40 --workers 3
    python build_cards.py report   --topic html

Output lives in build/<topic>/ (gitignored): sections.jsonl, cards.jsonl, failed.jsonl, skipped.jsonl.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
MAX_SECTION = 6000   # chars; larger sections are split at H3, then paragraphs
MIN_SECTION = 300    # chars; smaller sections are merged into their neighbour, never dropped
LOW_VALUE = {"specifications", "browser compatibility", "see also", "compatibility"}
# Per-topic policy. EXCLUDE: path prefixes never processed. LIGHT: reference pages processed briefly
# (one section per page, examples/polyfills dropped, capped) - the lesson links to the source for detail.
EXCLUDE = {"react": ["src/content/blog/", "src/content/community/"]}
LIGHT = {"javascript": ["files/en-us/web/javascript/reference/global_objects/", "files/en-us/web/javascript/reference/errors/"]}
LIGHT_DROP = {"examples", "example", "polyfill", "polyfills", "try it"}
LIGHT_MAX = 7000

PROMPT = """Aşağıda bir teknik dokümanın bir bölümü var. Görevin iki şey üretmek:

1. "lesson": Bu bölümün konusunu anlaşılır şekilde açıklayan, kısa bir Türkçe konu anlatımı (Markdown): konu nedir, nasıl çalışır, en önemli noktalar. Her ayrıntıya girme, ayrıntıyı kaynağa bırak (kaynak bağlantısını sistem ekler, sen link yazma). Genelde 2–5 paragraf ve gerekirse kısa bir kod örneği. Yalnız bölümdeki bilgiye dayan; bölümde olmayanı ekleme. Teknik adlar, kod ve örnekler orijinal kalsın.
2. "cards": Bölümdeki öğrenmeye değer bilgiler için soru-cevap kartı. Her kart tek bir bilgiyi yoklasın. Öğrenmeye değer bilgi yoksa boş liste ver. Bölümde olmayan hiçbir şeyi sorma. Girdi satırında "light": true varsa sayfa referans niteliğindedir: yalnızca en önemli 1–4 bilgiyi kart yap, aşırı ayrıntıya girme. Kolay aranır ayrıntıları, tarihçeyi, tarayıcı uyumluluk listelerini kart yapma.
   Her kartta: "question" (Türkçe), "answer" (Türkçe, kısa ve kesin), "evidence" (bölüm metninden BİREBİR kopyalanmış, değiştirilmemiş tek bir alıntı: bir ya da iki cümle ya da birkaç satır kod; kod, bağlantı ve biçimlendirme işaretleri dahil olabilir, karakterleri olduğu gibi kopyala). Kod ağırlıklı bölümlerde de kart üret: bu kod ne yapar, neden böyle yazılır, hangi çıktıyı verir, hangi hata yapılır gibi.

Yalnızca şu şekilde tek bir JSON nesnesi döndür, başka hiçbir metin yazma:
{"lesson": "...", "cards": [{"question": "...", "answer": "...", "evidence": "..."}]}

Başlık yolu: %(path)s

BÖLÜM:
%(text)s
"""


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm(text):
    return " ".join(text.split())


def flag_terms(lesson, cards, source):
    """Code terms used by the lesson/cards that do not occur in the source section (model's own knowledge = suspicious)."""
    def compact(t):
        return re.sub(r"\s+", "", t)
    src = compact(source)
    flags = []
    prose = re.sub(r"```.*?```", lambda m: "\n".join("`" + ln.strip() + "`" for ln in m.group(0).strip("`").splitlines()[1:] if ln.strip()), lesson, flags=re.S)
    for where, text in [("ders", prose)] + [("kart", c["question"] + " " + c["answer"]) for c in cards]:
        parts = text.split("`")
        if len(parts) % 2 == 0:  # unbalanced backticks: pairing is unreliable, skip
            continue
        for term in parts[1::2]:
            t = term.strip()
            # kısaltılmış örnek (`...`), yalın etiket (`<li>`), tek/çift harfli değer ('s') uydurma sayılmaz
            placeholder = "..." in t or "…" in t or re.fullmatch(r"</?\w+\s*/?>", t) or not re.search(r"[A-Za-z0-9_]{3,}", t)
            if len(t) >= 3 and not placeholder and compact(t) not in src and f"{where}:{t}" not in flags:
                flags.append(f"{where}:{t}")
    return flags


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_jsonl(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def entry_for(topic):
    entries = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    found = [e for e in entries if e["topic"] == topic]
    if not found:
        sys.exit(f"sources.json'da topic yok: {topic}")
    entry = found[0]
    if entry.get("include") is not True or entry.get("status") not in ("ok", "root"):
        sys.exit(f"{topic}: include/status uygun değil ({entry.get('include')}, {entry.get('status')})")
    return entry


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


# ---- fetch -------------------------------------------------------------

def cmd_fetch(args):
    entry = entry_for(args.topic)
    repo = entry["repo"]
    paths = entry.get("paths") or ["."]
    checkout = BUILD / args.topic / "checkout"
    if not (checkout / ".git").exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "--sparse",
            f"https://github.com/{repo}.git", str(checkout))
    patterns = "\n".join("/*" if p == "." else "/" + p.strip("/") for p in paths) + "\n"
    subprocess.run(["git", "sparse-checkout", "set", "--no-cone", "--stdin"], cwd=checkout, input=patterns,
                   check=True, text=True, capture_output=True)
    git("checkout", "HEAD", cwd=checkout)
    commit = git("rev-parse", "HEAD", cwd=checkout)
    files = [p for p in checkout.rglob("*") if p.is_file() and ".git" not in p.relative_to(checkout).parts
             and p.suffix.lower() in (".md", ".mdx")]
    (BUILD / args.topic / "fetch.json").write_text(json.dumps({"repo": repo, "commit": commit, "files": len(files)}), encoding="utf-8")
    print(json.dumps({"topic": args.topic, "commit": commit, "markdown_files": len(files)}))


# ---- split -------------------------------------------------------------

def frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            meta = {}
            for line in text[3:end].splitlines():
                match = re.match(r"^(\w+):\s*(.*?)\s*$", line)
                if match:
                    meta[match.group(1)] = match.group(2).strip("\"'")
            return meta, text[end + 4:].lstrip("\n")
    return {}, text


def sections_of(body, title):
    """Split at H2 outside code fences. Returns [(heading_path, text)]."""
    parts, heading, buf, fence = [], title, [], False
    for line in body.splitlines():
        if re.match(r"^\s{0,3}(```|~~~)", line):
            fence = not fence
        match = None if fence else re.match(r"^##\s+(.+?)\s*#*$", line)
        if match:
            parts.append((heading, "\n".join(buf).strip()))
            heading, buf = re.sub(r"\s*\{/\*.*?\*/\}\s*", " ", match.group(1)).strip(), []  # drop MDX anchor comments
        else:
            buf.append(line)
    parts.append((heading, "\n".join(buf).strip()))
    return [(h, t) for h, t in parts if t]


def split_large(text):
    """Split an oversized section at H3 / blank lines outside fences without cutting code."""
    chunks, current, fence = [], [], False
    for block in re.split(r"(\n\n+)", text):
        for line in block.splitlines():
            if re.match(r"^\s{0,3}(```|~~~)", line):
                fence = not fence
        current.append(block)
        if not fence and len("".join(current)) >= MAX_SECTION * 0.7 and block.strip():
            chunks.append("".join(current).strip())
            current = []
    if "".join(current).strip():
        chunks.append("".join(current).strip())
    return chunks


URL_RULES = {"react": ("src/content/", "https://react.dev/")}  # topic -> (repo path prefix, site root)


def source_url(entry, commit, rel, meta):
    rule = URL_RULES.get(entry["topic"])
    if rule and rel.startswith(rule[0]):
        path = re.sub(r"\.mdx?$", "", rel[len(rule[0]):])
        return rule[1] + re.sub(r"(^|/)index$", "", path)
    slug = meta.get("slug")
    if entry.get("site") and slug and "developer.mozilla.org" in entry["site"]:
        return f"{entry['site']}/en-US/docs/{slug}"
    return f"https://github.com/{entry['repo']}/blob/{commit}/{rel}"


def cmd_split(args):
    entry = entry_for(args.topic)
    base = BUILD / args.topic
    info = json.loads((base / "fetch.json").read_text(encoding="utf-8"))
    checkout = base / "checkout"
    sections, skipped = [], []
    for path in sorted(p for p in checkout.rglob("*") if p.is_file() and p.suffix.lower() in (".md", ".mdx")
                       and ".git" not in p.relative_to(checkout).parts):
        rel = path.relative_to(checkout).as_posix()
        if any(rel.startswith(prefix) for prefix in EXCLUDE.get(args.topic, ())):
            skipped.append({"file": rel, "reason": "excluded by topic policy"})
            continue
        light = any(rel.startswith(prefix) for prefix in LIGHT.get(args.topic, ()))
        meta, body = frontmatter(path.read_text(encoding="utf-8-sig"))
        title = meta.get("title") or path.stem
        url = source_url(entry, info["commit"], rel, meta)
        pieces = []
        for heading, text in sections_of(body, title):
            if heading.casefold() in LOW_VALUE:
                skipped.append({"file": rel, "heading": heading, "reason": "low-value heading", "chars": len(text)})
                continue
            if light and heading.casefold() in LIGHT_DROP:
                skipped.append({"file": rel, "heading": heading, "reason": "light page: examples dropped", "chars": len(text)})
                continue
            parts = [text] if light else (split_large(text) if len(text) > MAX_SECTION else [text])
            pieces.extend((heading, part) for part in parts)
        if light and pieces:  # one section per page, capped; the lesson links to the source for more
            joined = "\n\n".join(t if h == title else f"## {h}\n\n{t}" for h, t in pieces)
            if len(joined) > LIGHT_MAX:
                skipped.append({"file": rel, "reason": "light page capped", "chars": len(joined) - LIGHT_MAX})
                joined = joined[:LIGHT_MAX].rsplit("\n\n", 1)[0]
            pieces = [(title, joined)]
        merged = []
        for heading, text in pieces:  # tiny pieces join the previous one; nothing is dropped
            if merged and len(text) < MIN_SECTION:
                merged[-1][1] += "\n\n## " + heading + "\n\n" + text
            else:
                merged.append([heading, text])
        if len(merged) > 1 and len(merged[0][1]) < MIN_SECTION:  # tiny first piece joins the next
            first = merged.pop(0)
            merged[0][1] = first[1] + "\n\n## " + merged[0][0] + "\n\n" + merged[0][1]
            merged[0][0] = first[0]
        for index, (heading, text) in enumerate(merged):
            if len(text) < 80:
                skipped.append({"file": rel, "heading": heading, "reason": "under 80 chars after merge", "chars": len(text)})
                continue
            sections.append({"sectionId": sha(rel + "\0" + str(index) + "\0" + text)[:16], "topic": args.topic,
                             "group": entry.get("group"), "title": title, "headingPath": f"{title} > {heading}",
                             "file": rel, "sourceUrl": url, "commit": info["commit"], "light": light, "text": text})
    for name, rows in (("sections.jsonl", sections), ("skipped.jsonl", skipped)):
        (base / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    sizes = sorted(len(s["text"]) for s in sections)
    print(json.dumps({"topic": args.topic, "sections": len(sections), "skipped": len(skipped),
                      "median_chars": sizes[len(sizes) // 2] if sizes else 0, "max_chars": sizes[-1] if sizes else 0}))


# ---- generate ----------------------------------------------------------

def call_model(model, prompt, timeout):
    workdir = Path(tempfile.mkdtemp(prefix="oc-"))
    try:
        result = subprocess.run(["opencode", "run", "--pure", "--dir", str(workdir), "-m", model, prompt],
                                capture_output=True, text=True, encoding="utf-8", timeout=timeout, shell=(sys.platform == "win32"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if result.returncode != 0:
        raise RuntimeError("opencode exit " + str(result.returncode))
    return re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)


def parse_output(raw):
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("no JSON object in output")
    return json.loads(match.group(0))


def validate(value, section):
    if not isinstance(value, dict) or not isinstance(value.get("lesson"), str) or not value["lesson"].strip():
        raise ValueError("missing lesson")
    if not isinstance(value.get("cards"), list):
        raise ValueError("cards must be a list")
    haystack, cards = norm(section["text"]), []
    for item in value["cards"]:
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("question", "answer", "evidence")):
            raise ValueError("card fields must be nonempty text")
        if norm(item["evidence"]) not in haystack:
            raise ValueError("evidence is not a verbatim quote of the section")
        cards.append({"question": item["question"].strip(), "answer": item["answer"].strip(), "evidence": item["evidence"].strip()})
    return value["lesson"].strip(), cards


def generate_one(section, model, timeout):
    prompt = PROMPT % {"path": section["headingPath"], "text": section["text"]}
    raw = call_model(model, prompt, timeout)
    lesson, cards = validate(parse_output(raw), section)
    return {"sectionId": section["sectionId"], "model": model, "lesson": lesson, "cards": cards}


def cmd_generate(args):
    base = BUILD / args.topic
    sections = read_jsonl(base / "sections.jsonl")
    done = {row["sectionId"] for row in read_jsonl(base / "cards.jsonl")}
    todo = sorted((s for s in sections if s["sectionId"] not in done), key=lambda s: s["sectionId"])[: args.limit]  # hash order = spread-out sample
    print(f"{len(todo)} bölüm işlenecek ({len(done)} tamam, {len(sections)} toplam)", flush=True)

    def work(section):
        last = None
        for attempt in range(args.retries + 1):
            try:
                return section, generate_one(section, args.model, args.timeout), None
            except Exception as error:  # a bad section never stops the run
                last = f"{type(error).__name__}: {error}"
        return section, None, last

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for section, result, error in pool.map(work, todo):
            if result:
                append_jsonl(base / "cards.jsonl", result)
                print(f"ok  {section['headingPath'][:70]} -> {len(result['cards'])} kart", flush=True)
            else:
                append_jsonl(base / "failed.jsonl", {"sectionId": section["sectionId"], "headingPath": section["headingPath"], "error": error})
                print(f"HATA {section['headingPath'][:60]}: {error}", flush=True)


def cmd_report(args):
    base = BUILD / args.topic
    sections, rows, failed = read_jsonl(base / "sections.jsonl"), read_jsonl(base / "cards.jsonl"), read_jsonl(base / "failed.jsonl")
    cards = sum(len(r["cards"]) for r in rows)
    print(json.dumps({"sections": len(sections), "generated": len(rows), "failed_attempts": len(failed), "cards": cards,
                      "sections_without_cards": sum(1 for r in rows if not r["cards"]),
                      "avg_lesson_chars": int(sum(len(r["lesson"]) for r in rows) / max(len(rows), 1))}))


# ---- queue: paths / plan / next / ingest (resumable hand-off to sub-agents) ------

def cmd_paths(args):
    """Every source file of a topic with its section/char counts -> build/<topic>/paths.json."""
    base = BUILD / args.topic
    rows = {}
    for s in read_jsonl(base / "sections.jsonl"):
        row = rows.setdefault(s["file"], {"file": s["file"], "sourceUrl": s["sourceUrl"], "title": s["title"], "sections": 0, "chars": 0})
        row["sections"] += 1
        row["chars"] += len(s["text"])
    out = sorted(rows.values(), key=lambda r: r["file"])
    (base / "paths.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({"topic": args.topic, "files": len(out), "sections": sum(r["sections"] for r in out), "chars": sum(r["chars"] for r in out)}))


def cmd_plan(args):
    """Group not-yet-generated sections into batch files; existing batches are kept untouched."""
    base = BUILD / args.topic
    queue_path = base / "queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.exists() else []
    done = {r["sectionId"] for r in read_jsonl(base / "cards.jsonl")}
    queued = {sid for q in queue for sid in q["sectionIds"] if q["status"] == "pending"}
    todo = [s for s in read_jsonl(base / "sections.jsonl") if s["sectionId"] not in done and s["sectionId"] not in queued]
    number, batches, current, size = len(queue), [], [], 0
    for s in todo:  # file order is kept so a batch stays within neighbouring pages
        if current and (size + len(s["text"]) > args.batch_chars or len(current) >= args.batch_sections):
            batches.append(current)
            current, size = [], 0
        current.append(s)
        size += len(s["text"])
    if current:
        batches.append(current)
    for group in batches:
        number += 1
        name = f"{number:04d}"
        path = base / "batches" / f"{name}.in.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps({k: s[k] for k in ("sectionId", "headingPath", "light", "text")}, ensure_ascii=False) + "\n" for s in group),
                        encoding="utf-8", newline="\n")
        queue.append({"batch": name, "status": "pending", "sections": len(group), "chars": sum(len(s["text"]) for s in group),
                      "sectionIds": [s["sectionId"] for s in group]})
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    (base / "TASK.txt").write_text(PROMPT.split("Başlık yolu:")[0], encoding="utf-8", newline="\n")
    pending = [q for q in queue if q["status"] == "pending"]
    print(json.dumps({"topic": args.topic, "new_batches": len(batches), "pending": len(pending), "done": len(queue) - len(pending),
                      "pending_sections": sum(q["sections"] for q in pending)}))


def cmd_next(args):
    base = BUILD / args.topic
    queue = json.loads((base / "queue.json").read_text(encoding="utf-8"))
    pending = [q for q in queue if q["status"] in ("pending", "partial")]
    if not pending:
        print("kuyruk bitti")
        return
    q = pending[0]
    print(json.dumps({"batch": q["batch"], "sections": q["sections"], "chars": q["chars"],
                      "input": f"build/{args.topic}/batches/{q['batch']}.in.jsonl",
                      "output": f"build/{args.topic}/batches/{q['batch']}.out.jsonl"}))


def cmd_ingest(args):
    """Validate a batch's .out.jsonl (evidence must be verbatim) and append the good rows to cards.jsonl."""
    base = BUILD / args.topic
    queue_path = base / "queue.json"
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    entry = next(q for q in queue if q["batch"] == args.batch)
    sections = {s["sectionId"]: s for s in read_jsonl(base / "sections.jsonl")}
    out_path = base / "batches" / f"{args.batch}.out.jsonl"
    already = {r["sectionId"] for r in read_jsonl(base / "cards.jsonl")}
    ok = bad = 0
    got = set()
    out_lines = [ln for p in sorted((base / "batches").glob(f"{args.batch}*.out.jsonl"))  # NNNN.out + NNNNb.out
                 for ln in p.read_text(encoding="utf-8").splitlines()]
    for line in out_lines:
        try:
            value = json.loads(line)
            sid = value["sectionId"]
            if sid not in entry["sectionIds"]:
                raise ValueError("section not in this batch")
            lesson, cards = validate(value, sections[sid])
        except Exception as error:
            bad += 1
            append_jsonl(base / "failed.jsonl", {"batch": args.batch, "error": f"{type(error).__name__}: {error}"})
            continue
        got.add(sid)
        if sid not in already:
            lesson += f"\n\n[Daha fazla ayrıntı için kaynak]({sections[sid]['sourceUrl']})"
            row = {"sectionId": sid, "model": args.model, "lesson": lesson, "cards": cards}
            flags = flag_terms(lesson, cards, sections[sid]["text"])
            if flags:
                row["flags"] = flags
            append_jsonl(base / "cards.jsonl", row)
            ok += 1
    missing = [sid for sid in entry["sectionIds"] if sid not in got and sid not in already]
    entry["status"] = "done" if not missing else "partial"
    entry["missing"] = missing
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({"batch": args.batch, "ingested": ok, "rejected": bad, "missing": len(missing), "status": entry["status"]}))


OPENCODE_EXE = r"C:\Program Files\nodejs\node_modules\opencode-ai\bin\opencode.exe"


def run_opencode(model, prompt, timeout):
    """One opencode request. Real .exe, no shell (cmd.exe cuts multi-line arguments)."""
    import os
    exe = os.getenv("OPENCODE_EXE", OPENCODE_EXE)
    workdir = Path(tempfile.mkdtemp(prefix="oc-"))
    try:
        result = subprocess.run([exe, "run", "--pure", "--title", "cards", "--dir", str(workdir), "-m", model, prompt],
                                capture_output=True, text=True, encoding="utf-8", timeout=timeout, stdin=subprocess.DEVNULL)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    text = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout + result.stderr)
    if "usage limit" in text.lower():
        raise LimitReached(text.strip()[-200:])
    if result.returncode != 0:
        raise RuntimeError("opencode exit " + str(result.returncode))
    return text


class LimitReached(Exception):
    pass


def cmd_opencode(args):
    """Process pending batches with a free OpenCode model: ONE request per batch, stop at the first sign of the quota."""
    base = BUILD / args.topic
    queue = json.loads((base / "queue.json").read_text(encoding="utf-8"))
    task = (base / "TASK.txt").read_text(encoding="utf-8")
    sent = failures = 0
    for entry in queue:
        if entry["status"] not in ("pending", "partial") or sent >= args.max_requests:
            continue
        rows = read_jsonl(base / "batches" / f"{entry['batch']}.in.jsonl")
        if entry["status"] == "partial":
            rows = [r for r in rows if r["sectionId"] in set(entry.get("missing", []))]
        prompt = (task + "\nHer girdi satırı için çıktıda TAM BİR satır ver; her satır tek bir JSON nesnesi: "
                  '{"sectionId": "...", "lesson": "...", "cards": [...]}. Satırlar dışında hiçbir metin yazma.\n\nGİRDİ SATIRLARI:\n'
                  + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        sent += 1
        try:
            text = run_opencode(args.model, prompt, args.timeout)
        except LimitReached as error:
            print(f"KOTA DOLDU, durdum ({sent - 1} parti işlendi): {error}", flush=True)
            return
        except Exception as error:
            failures += 1
            print(f"parti {entry['batch']} hata: {type(error).__name__}: {error}", flush=True)
            if failures >= 2:
                print("üst üste hata, durdum", flush=True)
                return
            continue
        lines = [ln for ln in text.splitlines() if ln.strip().startswith("{")]
        out = base / "batches" / f"{entry['batch']}.out.jsonl"
        out.write_text("".join(ln.strip() + "\n" for ln in lines), encoding="utf-8", newline="\n")
        ns = argparse.Namespace(topic=args.topic, batch=entry["batch"], model=args.model)
        cmd_ingest(ns)
        queue = json.loads((base / "queue.json").read_text(encoding="utf-8"))
        failures = 0


RECARDS_TASK = """Aşağıda bir teknik dokümanın bir bölümü ve bu bölümden ZATEN üretilmiş sorular ("existing") var. Görevin: bölümde öğrenmeye değer olup henüz sorulmamış bilgiler için EK soru-cevap kartları üretmek.
- Mevcut sorularla aynı ya da çok benzer soru yazma.
- Kod, bağlantı ve biçimlendirme içeren yerler dahil bölümün her bilgisi kart olabilir. Kod ağırlıklı bölümlerde: bu kod ne yapar, neden böyle yazılır, hangi çıktıyı verir, hangi hata yapılır gibi kartlar üret.
- Her kartta: "question" (Türkçe), "answer" (Türkçe, kısa ve kesin), "evidence" (bölüm metninden BİREBİR kopyalanmış tek bir alıntı: bir ya da iki cümle ya da birkaç satır kod; karakterleri olduğu gibi kopyala).
- Bölümde olmayan hiçbir şeyi sorma. Eklenecek yeni bilgi yoksa boş liste ver.
Her girdi satırı için çıktıda tam bir satır: {"sectionId": "...", "cards": [{"question": "...", "answer": "...", "evidence": "..."}]}
"""


def _recards_check(row, section, existing_norm):
    """Validate extra cards of one output row. Returns (good_cards, problems)."""
    good, problems = [], []
    for item in row.get("cards", []):
        if not (isinstance(item, dict) and all(isinstance(item.get(k), str) and item[k].strip() for k in ("question", "answer", "evidence"))):
            problems.append("kart alanları eksik")
        elif norm(item["evidence"]) not in norm(section["text"]):
            problems.append("alıntı birebir değil: " + item["evidence"][:80])
        elif norm(item["question"]).casefold() in existing_norm:
            problems.append("mevcut soru tekrarı: " + item["question"][:60])
        else:
            good.append({"question": item["question"].strip(), "answer": item["answer"].strip(), "evidence": item["evidence"].strip()})
    return good, problems


def cmd_rplan(args):
    """Extra-card batches for sections generated under an older, stricter rule (lessons stay untouched)."""
    base = BUILD / args.topic
    queue = json.loads((base / "queue.json").read_text(encoding="utf-8"))
    sections = {s["sectionId"]: s for s in read_jsonl(base / "sections.jsonl")}
    rows = {r["sectionId"]: r for r in read_jsonl(base / "cards.jsonl")}
    old = {sid for q in queue if q["batch"] <= args.max_batch for sid in q["sectionIds"] if sid in rows}
    rq_path = base / "recards.json"
    rq = json.loads(rq_path.read_text(encoding="utf-8")) if rq_path.exists() else []
    queued = {sid for q in rq for sid in q["sectionIds"]}
    todo = [s for sid, s in sections.items() if sid in old and sid not in queued]
    groups, current, size = [], [], 0
    for s in todo:
        if current and (size + len(s["text"]) > args.batch_chars or len(current) >= args.batch_sections):
            groups.append(current)
            current, size = [], 0
        current.append(s)
        size += len(s["text"])
    if current:
        groups.append(current)
    for group in groups:
        name = f"RC{len(rq) + 1:04d}"
        path = base / "recards" / f"{name}.in.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps({"sectionId": s["sectionId"], "headingPath": s["headingPath"], "text": s["text"],
                                            "existing": [c["question"] for c in rows[s["sectionId"]]["cards"]]}, ensure_ascii=False) + "\n" for s in group),
                        encoding="utf-8", newline="\n")
        rq.append({"batch": name, "status": "pending", "sections": len(group), "chars": sum(len(s["text"]) for s in group),
                   "sectionIds": [s["sectionId"] for s in group]})
    rq_path.write_text(json.dumps(rq, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    (base / "TASK_RECARDS.txt").write_text(RECARDS_TASK, encoding="utf-8", newline="\n")
    print(json.dumps({"topic": args.topic, "new_batches": len(groups), "sections": sum(len(g) for g in groups),
                      "pending": sum(1 for q in rq if q["status"] != "done")}))


def _recards_rows(base, batch):
    entry = next(q for q in json.loads((base / "recards.json").read_text(encoding="utf-8")) if q["batch"] == batch)
    out = []
    for path in sorted((base / "recards").glob(f"{batch}*.out.jsonl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            out.append((path.name, number, line))
    return entry, out


def cmd_rverify(args):
    base = BUILD / args.topic
    sections = {s["sectionId"]: s for s in read_jsonl(base / "sections.jsonl")}
    rows = {r["sectionId"]: r for r in read_jsonl(base / "cards.jsonl")}
    entry, lines = _recards_rows(base, args.batch)
    seen, problems = set(), []
    for fname, number, line in lines:
        try:
            value = json.loads(line)
            sid = value["sectionId"]
            if sid not in entry["sectionIds"]:
                raise ValueError("sectionId not in this batch")
            _, issues = _recards_check(value, sections[sid], {norm(c["question"]).casefold() for c in rows[sid]["cards"]})
            problems += [f"{fname} satır {number}: {i}" for i in issues]
            seen.add(sid)
        except Exception as error:
            problems.append(f"{fname} satır {number}: {type(error).__name__}: {error}")
    print(json.dumps({"ok": len(seen), "missing": len([s for s in entry["sectionIds"] if s not in seen]), "problems": problems}, ensure_ascii=False, indent=1))


def cmd_ringest(args):
    """Append valid extra cards to the existing rows; lessons and earlier cards are kept."""
    base = BUILD / args.topic
    sections = {s["sectionId"]: s for s in read_jsonl(base / "sections.jsonl")}
    rows = read_jsonl(base / "cards.jsonl")
    index = {r["sectionId"]: r for r in rows}
    rq_path = base / "recards.json"
    rq = json.loads(rq_path.read_text(encoding="utf-8"))
    _, lines = _recards_rows(base, args.batch)
    entry = next(q for q in rq if q["batch"] == args.batch)  # the object that is saved below
    got, added, bad = set(), 0, 0
    for fname, number, line in lines:
        try:
            value = json.loads(line)
            sid = value["sectionId"]
            if sid not in entry["sectionIds"] or sid in got:
                raise ValueError("unexpected or duplicate sectionId")
        except Exception:
            bad += 1
            continue
        existing = {norm(c["question"]).casefold() for c in index[sid]["cards"]}
        good, issues = _recards_check(value, sections[sid], existing)
        bad += len(issues)
        got.add(sid)
        index[sid]["cards"].extend(good)
        added += len(good)
        index[sid].pop("flags", None)
        flags = flag_terms(index[sid]["lesson"], index[sid]["cards"], sections[sid]["text"])
        if flags:
            index[sid]["flags"] = flags
    tmp = base / "cards.jsonl.tmp"
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    tmp.replace(base / "cards.jsonl")
    entry["status"] = "done" if all(s in got for s in entry["sectionIds"]) else "partial"
    entry["missing"] = [s for s in entry["sectionIds"] if s not in got]
    rq_path.write_text(json.dumps(rq, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({"batch": args.batch, "added_cards": added, "rejected_cards": bad, "missing_sections": len(entry["missing"]), "status": entry["status"]}))


def cmd_flag(args):
    """Re-scan every generated row for terms the source section does not contain; stores them in row['flags']."""
    base = BUILD / args.topic
    sections = {s["sectionId"]: s for s in read_jsonl(base / "sections.jsonl")}
    rows = read_jsonl(base / "cards.jsonl")
    flagged = 0
    for row in rows:
        row.pop("flags", None)
        flags = flag_terms(row["lesson"], row["cards"], sections[row["sectionId"]]["text"])
        if flags:
            row["flags"] = flags
            flagged += 1
    tmp = base / "cards.jsonl.tmp"
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    tmp.replace(base / "cards.jsonl")
    print(json.dumps({"rows": len(rows), "flagged": flagged, "flagged_lessons": sum(1 for r in rows if any(f.startswith("ders:") for f in r.get("flags", []))),
                      "flagged_cards": sum(1 for r in rows if any(f.startswith("kart:") for f in r.get("flags", [])))}))


def cmd_status(args):
    """One-screen overview of every topic under build/: where we left off."""
    print(f"{'konu':24}{'bölüm':>7}{'üretilen':>10}{'kart':>7}{'bekleyen parti':>16}{'eksik':>7}{'ek-kart kalan':>15}{'işaretli':>9}")
    for base in sorted(p for p in BUILD.iterdir() if (p / "sections.jsonl").exists()):
        sections = len(read_jsonl(base / "sections.jsonl"))
        rows = read_jsonl(base / "cards.jsonl")
        queue = json.loads((base / "queue.json").read_text(encoding="utf-8")) if (base / "queue.json").exists() else []
        rq = json.loads((base / "recards.json").read_text(encoding="utf-8")) if (base / "recards.json").exists() else []
        pending = sum(1 for q in queue if q["status"] == "pending")
        missing = sum(len(q.get("missing", [])) for q in queue)
        rc_left = sum(q["sections"] for q in rq if q["status"] != "done")
        flagged = sum(1 for r in rows if r.get("flags"))
        print(f"{base.name:24}{sections:>7}{len(rows):>10}{sum(len(r['cards']) for r in rows):>7}"
              f"{(str(pending) if queue else 'plan yok'):>16}{missing:>7}{rc_left:>15}{flagged:>9}")


LINK_MARK = "[Daha fazla ayrıntı için kaynak]("


def cmd_lplan(args):
    """Rows whose lesson is flagged -> build/<topic>/fixes/FX0001.in.jsonl (lesson to be rewritten from the source only)."""
    base = BUILD / args.topic
    sections = {s["sectionId"]: s for s in read_jsonl(base / "sections.jsonl")}
    rows = [r for r in read_jsonl(base / "cards.jsonl") if any(f.startswith("ders:") for f in r.get("flags", []))]
    path = base / "fixes" / "FX0001.in.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"sectionId": r["sectionId"], "headingPath": sections[r["sectionId"]]["headingPath"],
                                        "text": sections[r["sectionId"]]["text"],
                                        "lesson": r["lesson"].split("\n\n" + LINK_MARK)[0],
                                        "suspicious_terms": [f.split(":", 1)[1] for f in r["flags"] if f.startswith("ders:")]},
                                       ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    (base / "TASK_LESSONFIX.txt").write_text(
        "Her girdi satırında bir bölüm metni (text), bu bölüm için yazılmış mevcut ders (lesson) ve dersin içinde geçen ama bölüm metninde OLMAYAN "
        "şüpheli terimler (suspicious_terms) var. Dersi yeniden yaz:\n"
        "- Yalnızca bölüm metnindeki bilgiye dayan. Şüpheli terimleri ve kendi bilginden gelen her ekleme/örneği çıkar; yerine bölüm metninde geçen "
        "kodu ya da kavramı kullan.\n- Konu anlatımı kısa ve anlaşılır kalsın (2–5 paragraf), Türkçe; teknik adlar ve kod orijinal. Kaynak linki ekleme, sistem ekler.\n"
        "Çıktı satırı: {\"sectionId\": \"...\", \"lesson\": \"<Markdown>\"}\n", encoding="utf-8", newline="\n")
    print(json.dumps({"fix_rows": len(rows)}))


def cmd_lingest(args):
    """Replace lessons with the rewritten ones, but only if the new lesson passes the same source check."""
    base = BUILD / args.topic
    sections = {s["sectionId"]: s for s in read_jsonl(base / "sections.jsonl")}
    rows = read_jsonl(base / "cards.jsonl")
    index = {r["sectionId"]: r for r in rows}
    fixed = still = bad = 0
    for path in sorted((base / "fixes").glob("FX*.out.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                sid, lesson = value["sectionId"], value["lesson"].strip()
                row = index[sid]
                assert lesson
            except Exception:
                bad += 1
                continue
            flags = [f for f in flag_terms(lesson, [], sections[sid]["text"])]
            if flags:
                still += 1  # new lesson still has suspicious terms: keep the old (flagged) one
                continue
            row["lesson"] = lesson + "\n\n" + LINK_MARK + sections[sid]["sourceUrl"] + ")"
            row["flags"] = [f for f in row.get("flags", []) if not f.startswith("ders:")]
            if not row["flags"]:
                row.pop("flags")
            fixed += 1
    tmp = base / "cards.jsonl.tmp"
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    tmp.replace(base / "cards.jsonl")
    print(json.dumps({"lessons_fixed": fixed, "still_flagged": still, "unreadable": bad}))


def cmd_verify(args):
    """Dry-run check of a batch's .out files: reports bad lines and non-verbatim evidence. Changes nothing."""
    base = BUILD / args.topic
    sections = {s["sectionId"]: s for s in read_jsonl(base / "sections.jsonl")}
    entry = next(q for q in json.loads((base / "queue.json").read_text(encoding="utf-8")) if q["batch"] == args.batch)
    seen, problems = set(), []
    for path in sorted((base / "batches").glob(f"{args.batch}*.out.jsonl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                value = json.loads(line)
                sid = value["sectionId"]
                if sid not in entry["sectionIds"]:
                    raise ValueError("sectionId not in this batch")
                validate(value, sections[sid])
                seen.add(sid)
            except Exception as error:
                problems.append(f"{path.name} satır {number}: {type(error).__name__}: {error}")
                if isinstance(value, dict) and value.get("sectionId") in sections:
                    for item in value.get("cards", []):
                        if isinstance(item, dict) and norm(item.get("evidence", "")) not in norm(sections[value["sectionId"]]["text"]):
                            problems.append(f"   alıntı birebir değil: {item.get('evidence', '')[:100]!r}")
    missing = [s for s in entry["sectionIds"] if s not in seen]
    print(json.dumps({"ok": len(seen), "missing": len(missing), "problems": problems}, ensure_ascii=False, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    for name in ("fetch", "split", "generate", "report", "paths", "plan", "next", "ingest", "opencode", "verify", "rplan", "rverify", "ringest", "flag", "lplan", "lingest"):
        p = sub.add_parser(name)
        p.add_argument("--topic", required=True)
        if name == "plan":
            p.add_argument("--batch-chars", type=int, default=30000)
            p.add_argument("--batch-sections", type=int, default=15)
        if name == "opencode":
            p.add_argument("--model", default="opencode/glm-5.3-flash-free")
            p.add_argument("--max-requests", type=int, default=5)
            p.add_argument("--timeout", type=int, default=420)
        if name == "rplan":
            p.add_argument("--max-batch", default="0017")
            p.add_argument("--batch-chars", type=int, default=80000)
            p.add_argument("--batch-sections", type=int, default=30)
        if name in ("rverify", "ringest"):
            p.add_argument("--batch", required=True)
        if name in ("ingest", "verify"):
            p.add_argument("--batch", required=True)
            p.add_argument("--model", default="claude-haiku-subagent")
        if name == "generate":
            p.add_argument("--model", default="opencode/glm-5.3-flash-free")
            p.add_argument("--limit", type=int, default=40)
            p.add_argument("--workers", type=int, default=3)
            p.add_argument("--retries", type=int, default=1)
            p.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    {"fetch": cmd_fetch, "split": cmd_split, "generate": cmd_generate, "report": cmd_report, "paths": cmd_paths,
     "plan": cmd_plan, "next": cmd_next, "ingest": cmd_ingest, "opencode": cmd_opencode, "verify": cmd_verify,
     "flag": cmd_flag, "status": cmd_status, "lplan": cmd_lplan, "lingest": cmd_lingest, "rplan": cmd_rplan, "rverify": cmd_rverify, "ringest": cmd_ringest}[args.command](args)


if __name__ == "__main__":
    main()
