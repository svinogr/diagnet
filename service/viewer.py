#!/usr/bin/env python3
"""Локальный дашборд сетевой диагностики.

- Журнал проверок (netdiag_results.md) + журнал по узлам (hop_results.md)
- Запуск проверок по кнопке
- Настройки диагностики (config.bat) и расписаний
- Управление задачами Планировщика (основная + проверка по узлам)

Только стандартная библиотека. Слушает 127.0.0.1:8471.
"""
import ctypes
import json
import os
import re
import socket
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # папка service\
KIT_DIR = os.path.dirname(BASE_DIR)                     # корень набора
DATA_DIR = os.path.join(KIT_DIR, "data")
MD_LOG = os.path.join(DATA_DIR, "netdiag_results.md")
HOP_LOG = os.path.join(DATA_DIR, "hop_results.md")
WRAPPER = os.path.join(BASE_DIR, "netdiag_wrapper.bat")
HOPCHECK = os.path.join(BASE_DIR, "hopcheck.ps1")
CONFIG = os.path.join(BASE_DIR, "config.bat")
VIEWER_LOG = os.path.join(BASE_DIR, "viewer.log")
FAVICON = os.path.join(BASE_DIR, "icon.ico")
HOST, PORT = "127.0.0.1", 8471
TASK_NAME = "NetDiagHourly"
HOP_TASK_NAME = "NetDiagHopCheck"

RUN_LOCK = threading.Lock()
RUN_STATE = {"running": False}
HOP_LOCK = threading.Lock()
HOP_STATE = {"running": False}

# ---------------------------------------------------------------- конфиг

CONFIG_DEFAULTS = {
    "PING_COUNT": "30",
    "GW_MODE": "auto",          # auto | fixed
    "GW_FIXED": "192.168.0.1",
    "DO_PING_ROUTER": "1",
    "DO_PING_8888": "1",
    "DO_NSLOOKUP": "1",
    "NSLOOKUP_HOST": "ya.ru",
    "DO_CURL1": "1",
    "CURL1_URL": "https://ya.ru",
    "DO_CURL2": "1",
    "CURL2_URL": "https://www.google.com",
    "DO_DNS_UDP": "1",
    "DNS1": "8.8.8.8",
    "DNS2": "77.88.8.8",
    "DO_DNS_TCP": "1",
    "DNS_TCP_SERVER": "8.8.8.8",
    "DO_TRACERT": "1",
    "TRACE_HOST": "ya.ru",
    "TRACE_HOPS": "15",
    "HOP_TARGET": "ya.ru",
    "HOP_COUNT": "1000",
    "HOP_INTERVAL": "30",
}
CONFIG_ORDER = list(CONFIG_DEFAULTS.keys())
INT_KEYS = {"PING_COUNT": (1, 100), "TRACE_HOPS": (1, 50), "HOP_COUNT": (10, 5000),
            "HOP_INTERVAL": (10, 1440)}
BOOL_KEYS = {k for k in CONFIG_DEFAULTS if k.startswith("DO_")}
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SET_RE = re.compile(r'^set\s+"([A-Z0-9_]+)=(.*)"\s*$', re.M)
# Имена хостов и URL - только по белому списку символов: значения попадают
# в cmd и PowerShell, кавычки и спецсимволы там опасны.
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/-]*)?$")
HOST_KEYS = ("NSLOOKUP_HOST", "TRACE_HOST", "HOP_TARGET")
URL_KEYS = ("CURL1_URL", "CURL2_URL")
# Запросы принимаются только от самого дашборда (защита от CSRF и DNS rebinding)
ALLOWED_HOSTS = {"127.0.0.1:%d" % PORT, "localhost:%d" % PORT}
ALLOWED_ORIGINS = {"http://" + h for h in ALLOWED_HOSTS}


def valid_ip(val):
    parts = val.split(".")
    return (len(parts) == 4
            and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts))


def read_config():
    cfg = dict(CONFIG_DEFAULTS)
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="ascii", errors="ignore") as f:
            for key, val in SET_RE.findall(f.read()):
                if key in cfg:
                    cfg[key] = val.strip()
    return cfg


def validate_config(data):
    """Вернуть (cfg, None) или (None, сообщение об ошибке)."""
    if not isinstance(data, dict):
        return None, "неверный формат"
    cfg = read_config()
    for key, val in data.items():
        if key not in CONFIG_DEFAULTS:
            continue
        val = str(val).strip()
        if key in BOOL_KEYS:
            if val not in ("0", "1"):
                return None, "%s: ожидалось 0 или 1" % key
        elif key in INT_KEYS:
            lo, hi = INT_KEYS[key]
            try:
                n = int(val)
            except ValueError:
                return None, "%s: ожидалось число" % key
            if not (lo <= n <= hi):
                return None, "%s: диапазон %d..%d" % (key, lo, hi)
            val = str(n)
        elif key == "GW_MODE":
            if val not in ("auto", "fixed"):
                return None, "GW_MODE: auto или fixed"
        elif key in ("GW_FIXED", "DNS1", "DNS2", "DNS_TCP_SERVER"):
            if not valid_ip(val):
                return None, "%s: неверный IP-адрес" % key
        elif key in HOST_KEYS:
            if not HOSTNAME_RE.match(val):
                return None, "%s: допустимы только латиница, цифры, точка и дефис" % key
        elif key in URL_KEYS:
            if len(val) > 300 or not URL_RE.match(val):
                return None, "%s: нужен адрес вида https://host/path (без параметров)" % key
        else:
            return None, "%s: неизвестный параметр" % key
        cfg[key] = val
    if cfg["GW_MODE"] == "fixed" and not valid_ip(cfg["GW_FIXED"]):
        return None, "GW_FIXED: неверный IP-адрес"
    return cfg, None


def write_config(cfg):
    lines = [
        "REM Diagnostics configuration. Created by the dashboard - do not edit manually.",
        "REM Use the Settings tab of the web dashboard.",
    ]
    lines += ['set "%s=%s"' % (k, cfg[k]) for k in CONFIG_ORDER]
    with open(CONFIG, "w", encoding="ascii", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


# ---------------------------------------------------------------- журналы

ENTRY_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*$", re.M)
RESULT_RE = re.compile(r"\*\*Результат:\*\*\s*(.+)")
CODE_RE = re.compile(r"код (\d+)")
LOSS_EN_RE = re.compile(r"Lost = \d+ \((\d+)% loss\)")
LOSS_RU_RE = re.compile(r"\((\d+)% потерь\)")
DNS_RE = re.compile(r"DNS/(UDP|TCP) ([\d.]+): (\S+)")
CURL_RE = re.compile(r"connect: ([\d.]+)s tls: ([\d.]+)s total: ([\d.]+)s")


def summarize(body):
    parts = []
    losses = LOSS_EN_RE.findall(body) or LOSS_RU_RE.findall(body)
    if losses:
        parts.append("потери ping: " + "/".join(l + "%" for l in losses))
    dns = DNS_RE.findall(body)
    if dns:
        bad = ["%s/%s" % (proto, ip) for proto, ip, st in dns if st != "OK"]
        parts.append("DNS: " + (", ".join(bad) + " не отвечают" if bad else "все отвечают"))
    if "DNS request timed out" in body:
        parts.append("системный DNS: %d таймаутов" % body.count("DNS request timed out"))
    curls = CURL_RE.findall(body)
    if curls:
        totals = [float(c[2]) for c in curls]
        parts.append("curl total: макс %.2fс (%d шт.)" % (max(totals), len(curls)))
    hoploss = re.findall(r"\*\*\* HOP (\d+) [\d.]+: LOSS ([\d.]+)% \*\*\*", body)
    if hoploss:
        parts.append("потери на узлах: " + ", ".join("hop %s (%s%%)" % h for h in hoploss))
    return " · ".join(parts)


def parse_entries(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    matches = list(ENTRY_RE.finditer(text))
    entries = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[m.end():end]
        rm = RESULT_RE.search(chunk)
        result_raw = rm.group(1).strip() if rm else "?"
        cm = CODE_RE.search(result_raw)
        body = ""
        fm = re.search(r"```text\n(.*?)```", chunk, re.S)
        if fm:
            body = fm.group(1).strip("\n")
        entries.append({
            "ts": m.group(1),
            "ok": result_raw.startswith("OK"),
            "code": int(cm.group(1)) if cm else 0,
            "summary": summarize(body),
            "body": body,
        })
    entries.reverse()
    return entries


def entries_payload(path):
    entries = parse_entries(path)
    errors = sum(1 for e in entries if not e["ok"])
    return {
        "entries": entries,
        "stats": {
            "total": len(entries),
            "errors": errors,
            "ok_pct": round(100 * (len(entries) - errors) / len(entries)) if entries else 100,
            "last_error": next((e["ts"] for e in entries if not e["ok"]), None),
        },
    }


# ---------------------------------------------------------------- задачи

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def schtasks(args, elevated):
    """Запустить schtasks; elevated=True - через UAC (Start-Process -Verb RunAs)."""
    if is_admin() or not elevated:
        proc = subprocess.run(["schtasks"] + args, capture_output=True,
                              encoding="cp866", errors="replace",
                              creationflags=NO_WINDOW, timeout=120)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    argline = " ".join('"%s"' % a.replace('"', '\\"') for a in args)
    ps = ("Start-Process -FilePath schtasks.exe -ArgumentList '%s' "
          "-Verb RunAs -Wait" % argline.replace("'", "''"))
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, encoding="cp866", errors="replace",
                          creationflags=NO_WINDOW, timeout=180)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


PS_TASK_QUERY = (
    "$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; "
    "if ($t) { "
    "$i = $t | Get-ScheduledTaskInfo; "
    "$iv = ''; try { $iv = $t.Triggers[0].Repetition.Interval } catch {}; "
    "$sb = ''; try { $sb = $t.Triggers[0].StartBoundary } catch {}; "
    "[pscustomobject]@{installed=1; state=[string]$t.State; "
    "lastRun=[string]$i.LastRunTime; lastResult=$i.LastTaskResult; "
    "nextRun=[string]$i.NextRunTime; interval=$iv; startTime=$sb} | ConvertTo-Json -Compress "
    "} else { '{\"installed\":0}' }"
)


def task_query(name=TASK_NAME):
    last_err = "unknown"
    for attempt in range(3):
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-Command", PS_TASK_QUERY % name],
                                  capture_output=True, encoding="utf-8", errors="replace",
                                  creationflags=NO_WINDOW, timeout=60)
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout.strip())
                iv = data.get("interval") or ""
                mm = re.match(r"PT(\d+)M", iv)
                if mm:
                    data["minutes"] = int(mm.group(1))
                sb = data.get("startTime") or ""
                if len(sb) >= 16 and sb[10] == "T":
                    data["startTimeShort"] = sb[11:16]
                lr = data.get("lastResult")
                if lr == 267011:
                    data["lastResultText"] = "не запускалась"
                elif lr == 267009:
                    data["lastResultText"] = "выполняется"
                else:
                    data["lastResultText"] = ("OK" if lr == 0 else str(lr)) if lr is not None else "?"
                data["admin"] = is_admin()
                return data
            last_err = "rc=%s out=%s" % (proc.returncode, (proc.stdout or proc.stderr or "")[:200])
        except Exception as exc:
            last_err = repr(exc)
        time.sleep(1.5)
    log_line("task_query(%s) failed: %s" % (name, last_err))
    return {"installed": 0, "error": True}


def schtasks_error(rc, out):
    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    tail = lines[-1] if lines else ""
    return ("schtasks вернул код %s: %s" % (rc, tail))[:300]


def task_install(minutes):
    minutes = max(5, min(720, int(minutes)))
    args = ["/Create", "/F", "/TN", TASK_NAME, "/TR", WRAPPER,
            "/SC", "MINUTE", "/MO", str(minutes)]
    return schtasks(args, elevated=True)


def task_delete():
    return schtasks(["/Delete", "/TN", TASK_NAME, "/F"], elevated=True)


def hop_task_install(minutes):
    minutes = max(10, min(1440, int(minutes)))
    tr = 'powershell -NoProfile -ExecutionPolicy Bypass -File "%s"' % HOPCHECK
    args = ["/Create", "/F", "/TN", HOP_TASK_NAME, "/TR", tr,
            "/SC", "MINUTE", "/MO", str(minutes)]
    return schtasks(args, elevated=True)


def hop_task_delete():
    return schtasks(["/Delete", "/TN", HOP_TASK_NAME, "/F"], elevated=True)


def hop_run_async():
    if not HOP_LOCK.acquire(blocking=False):
        return False
    HOP_STATE["running"] = True

    def worker():
        try:
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", HOPCHECK], cwd=BASE_DIR,
                           creationflags=NO_WINDOW, timeout=6 * 3600)
        except Exception:
            log_line(traceback.format_exc())
        finally:
            HOP_STATE["running"] = False
            HOP_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    return True


# ---------------------------------------------------------------- страница

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="/favicon.ico?v=2">
<title>Диагностика сети</title>
<style>
:root { --bg:#0d1117; --panel:#161b22; --border:#30363d; --fg:#c9d1d9; --muted:#8b949e;
        --ok:#3fb950; --err:#f85149; --accent:#58a6ff; }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font:14px/1.5 system-ui,Segoe UI,Arial;
       margin:0 auto; padding:20px; max-width:1100px; }
h1 { font-size:20px; margin:0 0 12px; }
h2 { font-size:16px; margin:20px 0 10px; }
nav { margin-bottom:16px; }
nav button { border-radius:6px 6px 0 0; }
nav button.active { background:var(--accent); border-color:var(--accent); color:#0d1117; font-weight:600; }
.stats { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:14px; }
.card { background:var(--panel); border:1px solid var(--border); border-radius:8px;
        padding:8px 14px; min-width:110px; }
.card .num { font-size:22px; font-weight:600; }
.card .lbl { color:var(--muted); font-size:12px; }
.controls { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:16px; }
button { background:#21262d; color:var(--fg); border:1px solid var(--border);
         border-radius:6px; padding:7px 14px; cursor:pointer; font-size:14px; }
button:hover:not(:disabled) { border-color:var(--accent); color:var(--accent); }
button:disabled { opacity:.5; cursor:default; }
#runBtn, .primary { background:#1f6feb; border-color:#1f6feb; color:#fff; font-weight:600; }
#runBtn:hover:not(:disabled), .primary:hover:not(:disabled) { background:#388bfd; color:#fff; }
.danger { border-color:var(--err); color:var(--err); }
.danger:hover:not(:disabled) { border-color:var(--err); color:#fff; background:var(--err); }
label { color:var(--muted); display:flex; gap:5px; align-items:center; }
#status { color:var(--muted); }
details { background:var(--panel); border:1px solid var(--border); border-radius:8px; margin-bottom:8px; }
details[open] { border-color:var(--accent); }
summary { padding:9px 12px; cursor:pointer; display:flex; gap:10px; align-items:baseline;
          flex-wrap:wrap; list-style:none; }
summary::-webkit-details-marker { display:none; }
.badge { font-weight:700; font-size:12px; padding:2px 8px; border-radius:10px; white-space:nowrap; }
.badge.ok { background:rgba(63,185,80,.15); color:var(--ok); }
.badge.err { background:rgba(248,81,73,.15); color:var(--err); }
.ts { color:var(--muted); font-family:Consolas,monospace; }
.sum { color:var(--muted); font-size:12px; flex:1; min-width:200px; }
pre { margin:0; padding:10px 14px; border-top:1px solid var(--border); overflow-x:auto;
      font:12px/1.45 Consolas,monospace; color:#a5d6ff; white-space:pre-wrap; }
.hl { background:rgba(248,81,73,.18); color:#ffa198; display:inline-block; width:100%; }
.err-text { color:var(--err); font-weight:600; }
form.settings { background:var(--panel); border:1px solid var(--border); border-radius:8px;
                padding:14px 16px; }
.row { display:flex; gap:16px; flex-wrap:wrap; align-items:center; margin:8px 0; }
.row label { color:var(--fg); }
.row input[type=text], .row input[type=number], .row select, select {
  background:#0d1117; border:1px solid var(--border); color:var(--fg);
  border-radius:6px; padding:5px 8px; font-size:14px; }
.row input[type=number] { width:80px; }
.row .grow { flex:1; min-width:180px; }
.row .grow input[type=text] { width:100%; }
.hint { color:var(--muted); font-size:12px; margin:6px 0; }
.taskline { margin:6px 0; }
.taskline b { color:var(--fg); }
.grp { border:1px solid var(--border); border-radius:6px; padding:6px 14px 10px;
       margin:12px 0; }
.grp-title { color:var(--accent); font-weight:600; font-size:13px; margin:2px 0 6px; }
</style>
</head>
<body>
<h1>Диагностика сети</h1>
<nav>
  <button id="tabJournal" class="active" onclick="showTab('journal')">Журнал</button>
  <button id="tabSettings" onclick="showTab('settings')">Настройки</button>
</nav>

<section id="journal">
  <div class="stats" id="stats"></div>
  <div class="controls">
    <select id="logSel" onchange="load()">
      <option value="main">Основной журнал</option>
      <option value="hops">Журнал по узлам</option>
    </select>
    <button id="runBtn" onclick="runNow()">Запустить проверку сейчас</button>
    <button onclick="load()">Обновить</button>
    <label><input type="checkbox" id="onlyErr"> Только ошибки</label>
    <label><input type="checkbox" id="auto"> Автообновление (60 с)</label>
    <span id="status"></span>
  </div>
  <main id="list"></main>
</section>

<section id="settings" style="display:none">
  <h2>Основная проверка</h2>
  <form class="settings" onsubmit="return false">
    <div class="grp">
      <div class="grp-title">Ping</div>
      <div class="row">
        <label>Число ping-пакетов <input type="number" id="PING_COUNT" min="1" max="100"></label>
        <label>Роутер:
          <select id="GW_MODE">
            <option value="auto">автоопределение шлюза</option>
            <option value="fixed">вручную</option>
          </select>
        </label>
        <label>IP роутера <input type="text" id="GW_FIXED" style="width:120px"></label>
      </div>
      <div class="row">
        <label><input type="checkbox" id="DO_PING_ROUTER"> Ping роутера</label>
        <label><input type="checkbox" id="DO_PING_8888"> Ping 8.8.8.8</label>
      </div>
    </div>
    <div class="grp">
      <div class="grp-title">DNS</div>
      <div class="row">
        <label><input type="checkbox" id="DO_NSLOOKUP"> NSLookup (системный резолвер)</label>
        <label><input type="checkbox" id="DO_DNS_UDP"> DNS по UDP напрямую</label>
        <label><input type="checkbox" id="DO_DNS_TCP"> DNS по TCP напрямую</label>
      </div>
      <div class="row">
        <label>Хост для запросов <input type="text" id="NSLOOKUP_HOST" style="width:180px"></label>
      </div>
      <div class="row">
        <label>DNS-сервер #1 (UDP) <input type="text" id="DNS1" style="width:120px"></label>
        <label>DNS-сервер #2 (UDP) <input type="text" id="DNS2" style="width:120px"></label>
        <label>DNS-сервер для TCP-проверки <input type="text" id="DNS_TCP_SERVER" style="width:120px"></label>
      </div>
    </div>
    <div class="grp">
      <div class="grp-title">HTTPS (время соединения)</div>
      <div class="row">
        <label><input type="checkbox" id="DO_CURL1"> Проверка #1</label>
        <input type="text" id="CURL1_URL" class="grow" placeholder="https://...">
      </div>
      <div class="row">
        <label><input type="checkbox" id="DO_CURL2"> Проверка #2</label>
        <input type="text" id="CURL2_URL" class="grow" placeholder="https://...">
      </div>
    </div>
    <div class="grp">
      <div class="grp-title">Трассировка</div>
      <div class="row">
        <label><input type="checkbox" id="DO_TRACERT"> Выполнять</label>
        <label>Хост <input type="text" id="TRACE_HOST" style="width:160px"></label>
        <label>Лимит хопов <input type="number" id="TRACE_HOPS" min="1" max="50"></label>
      </div>
    </div>
    <div class="row">
      <button class="primary" onclick="saveConfig()">Сохранить параметры</button>
      <span id="cfgStatus"></span>
    </div>
  </form>

  <h2>Расписание автоматической проверки</h2>
  <div class="taskline" id="taskInfo">Загрузка…</div>
  <div class="row">
    <label>Интервал: каждые <input type="number" id="interval" min="5" max="720" value="60"> минут</label>
    <button class="primary" onclick="installTask()">Установить / обновить задачу</button>
    <button class="danger" onclick="deleteTask()">Удалить задачу</button>
  </div>
  <div class="hint">При установке/удалении может появиться запрос UAC — нажмите «Да».</div>

  <h2>Проверка по узлам (стиль WinMTR)</h2>
  <div class="taskline" id="hopInfo">Загрузка…</div>
  <div class="row">
    <label>Цель <input type="text" id="HOP_TARGET" style="width:140px"></label>
    <label>Пакетов на узел <input type="number" id="HOP_COUNT" min="10" max="5000"></label>
    <label>Интервал: каждые <input type="number" id="HOP_INTERVAL" min="10" max="1440" value="30"> минут</label>
  </div>
  <div class="row">
    <button class="primary" onclick="saveHopConfig()">Сохранить параметры</button>
    <button class="primary" onclick="installHopTask()">Установить задачу</button>
    <button class="danger" onclick="deleteHopTask()">Удалить задачу</button>
    <button onclick="runHopNow()">Запустить сейчас</button>
    <span id="hopStatus"></span>
  </div>
  <div class="hint">Пингует каждый узел маршрута. 1000 пакетов ≈ 17 минут (узлы параллельно).
  Результаты — в «Журнале по узлам».</div>
</section>

<script>
const BOOL_KEYS = ["DO_PING_ROUTER","DO_PING_8888","DO_NSLOOKUP","DO_CURL1","DO_CURL2",
                   "DO_DNS_UDP","DO_DNS_TCP","DO_TRACERT"];
const STR_KEYS = ["PING_COUNT","GW_MODE","GW_FIXED","NSLOOKUP_HOST","CURL1_URL","CURL2_URL",
                  "DNS1","DNS2","DNS_TCP_SERVER","TRACE_HOST","TRACE_HOPS",
                  "HOP_TARGET","HOP_COUNT","HOP_INTERVAL"];

function showTab(name) {
  document.getElementById("journal").style.display = name === "journal" ? "" : "none";
  document.getElementById("settings").style.display = name === "settings" ? "" : "none";
  document.getElementById("tabJournal").classList.toggle("active", name === "journal");
  document.getElementById("tabSettings").classList.toggle("active", name === "settings");
  if (name === "settings") { loadConfig(); loadTask(); loadHopTask(); }
}

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function highlight(body) {
  return body.split("\\n").map(line => {
    const bad = /TIMEOUT|FAIL|timed out|Превышен интервал|LOSS \\d/i.test(line);
    return bad ? '<span class="hl">' + esc(line) + '</span>' : esc(line);
  }).join("\\n");
}

let entries = [];
function render() {
  const onlyErr = document.getElementById("onlyErr").checked;
  const list = document.getElementById("list");
  list.innerHTML = "";
  for (const e of entries) {
    if (onlyErr && e.ok) continue;
    const d = document.createElement("details");
    const badge = e.ok
      ? '<span class="badge ok">OK</span>'
      : '<span class="badge err">ОШИБКА' + (e.code ? " " + e.code : "") + '</span>';
    const sum = e.summary ? '<span class="sum">' + esc(e.summary) + '</span>' : "";
    d.innerHTML = "<summary>" + badge + '<span class="ts">' + e.ts + "</span>" + sum + "</summary>" +
                  "<pre>" + highlight(e.body) + "</pre>";
    list.appendChild(d);
  }
}

function logUrl() {
  return document.getElementById("logSel").value === "hops" ? "/api/hops" : "/api/entries";
}

async function load() {
  const r = await fetch(logUrl());
  const data = await r.json();
  entries = data.entries;
  const s = data.stats;
  document.getElementById("stats").innerHTML =
    '<div class="card"><div class="num">' + s.total + '</div><div class="lbl">всего запусков</div></div>' +
    '<div class="card"><div class="num" style="color:var(--err)">' + s.errors + '</div><div class="lbl">с ошибкой</div></div>' +
    '<div class="card"><div class="num" style="color:var(--ok)">' + s.ok_pct + '%</div><div class="lbl">успешных</div></div>' +
    '<div class="card"><div class="num" style="font-size:15px">' + (s.last_error ? s.last_error : "—") +
    '</div><div class="lbl">последняя ошибка</div></div>';
  render();
}

async function runNow() {
  const btn = document.getElementById("runBtn");
  const st = document.getElementById("status");
  btn.disabled = true;
  st.textContent = "Выполняется… (~2 мин), вкладку можно не ждать — запись появится в списке";
  try {
    const r = await fetch("/api/run", { method: "POST" });
    const data = await r.json();
    if (data.rc === 0) { st.innerHTML = '<span style="color:var(--ok)">Готово: OK</span>'; }
    else if (data.rc !== undefined) { st.innerHTML = '<span class="err-text">Готово: ОШИБКА, код ' + data.rc + '</span>'; }
    else { st.innerHTML = '<span class="err-text">' + esc(data.error || "ошибка запуска") + '</span>'; }
  } catch (e) {
    st.innerHTML = '<span class="err-text">Сервер недоступен</span>';
  }
  btn.disabled = false;
  load();
}

async function loadConfig() {
  const r = await fetch("/api/config");
  const cfg = await r.json();
  for (const k of Object.keys(cfg)) {
    const el = document.getElementById(k);
    if (!el) continue;
    if (BOOL_KEYS.includes(k)) el.checked = cfg[k] === "1";
    else el.value = cfg[k];
  }
}

async function saveConfig() {
  const cfg = {};
  for (const k of STR_KEYS) cfg[k] = document.getElementById(k).value;
  for (const k of BOOL_KEYS) cfg[k] = document.getElementById(k).checked ? "1" : "0";
  const st = document.getElementById("cfgStatus");
  const r = await fetch("/api/config", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(cfg)
  });
  if (r.ok) { st.innerHTML = '<span style="color:var(--ok)">Сохранено</span>'; }
  else {
    const data = await r.json().catch(() => ({}));
    st.innerHTML = '<span class="err-text">' + esc(data.error || ("Ошибка " + r.status)) + '</span>';
  }
}

function taskHtml(t, label) {
  if (t.error) return label + ': <span class="err-text">ошибка опроса задачи</span> — подождите и обновите страницу.';
  if (!t.installed) return label + ": задача <b>не установлена</b>.";
  let h = label + ": <b>установлена</b> · состояние: <b>" + esc(t.state || "?") + "</b>";
  if (t.minutes) h += " · интервал: <b>каждые " + t.minutes + " мин</b>";
  if (t.startTimeShort && !t.minutes) h += " · время запуска: <b>" + esc(t.startTimeShort) + "</b>";
  h += "<br>последний запуск: <b>" + esc(t.lastRun || "?") + "</b> · результат: <b>" +
       esc(t.lastResultText || "?") + "</b> · следующий: <b>" + esc(t.nextRun || "—") + "</b>";
  return h;
}

async function loadTask() {
  const r = await fetch("/api/task");
  document.getElementById("taskInfo").innerHTML = taskHtml(await r.json(), "Автопроверка");
}
async function installTask() {
  const el = document.getElementById("taskInfo");
  el.textContent = "Создание/обновление задачи… если появился запрос UAC — нажмите «Да».";
  const r = await fetch("/api/task/install", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({minutes: document.getElementById("interval").value})
  });
  const data = await r.json();
  if (r.ok && data.ok) { el.innerHTML = '<span style="color:var(--ok)">Задача установлена.</span>'; }
  else { el.innerHTML = '<span class="err-text">Ошибка: ' + esc(data.error || r.status) + '</span>'; }
  loadTask();
}
async function deleteTask() {
  if (!confirm("Удалить задачу автоматической проверки?")) return;
  const el = document.getElementById("taskInfo");
  el.textContent = "Удаление задачи… если появился запрос UAC — нажмите «Да».";
  const r = await fetch("/api/task/delete", { method: "POST" });
  const data = await r.json();
  if (r.ok && data.ok) { el.innerHTML = '<span style="color:var(--ok)">Задача удалена.</span>'; }
  else { el.innerHTML = '<span class="err-text">Ошибка: ' + esc(data.error || r.status) + '</span>'; }
  loadTask();
}

async function loadHopTask() {
  const r = await fetch("/api/hoptask");
  document.getElementById("hopInfo").innerHTML = taskHtml(await r.json(), "Проверка по узлам");
}
async function saveHopConfig() {
  const r = await fetch("/api/config", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({HOP_TARGET: document.getElementById("HOP_TARGET").value,
                          HOP_COUNT: document.getElementById("HOP_COUNT").value,
                          HOP_INTERVAL: document.getElementById("HOP_INTERVAL").value})
  });
  const st = document.getElementById("hopStatus");
  if (r.ok) { st.innerHTML = '<span style="color:var(--ok)">Сохранено</span>'; }
  else { const d = await r.json().catch(() => ({})); st.innerHTML = '<span class="err-text">' + esc(d.error || r.status) + '</span>'; }
}
async function installHopTask() {
  await saveHopConfig();
  const el = document.getElementById("hopStatus");
  el.textContent = "Создание задачи… если появился запрос UAC — нажмите «Да».";
  const r = await fetch("/api/hoptask/install", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({minutes: document.getElementById("HOP_INTERVAL").value})
  });
  const data = await r.json();
  if (r.ok && data.ok) { el.innerHTML = '<span style="color:var(--ok)">Задача установлена.</span>'; }
  else { el.innerHTML = '<span class="err-text">Ошибка: ' + esc(data.error || r.status) + '</span>'; }
  loadHopTask();
}
async function deleteHopTask() {
  if (!confirm("Удалить задачу проверки по узлам?")) return;
  const el = document.getElementById("hopStatus");
  const r = await fetch("/api/hoptask/delete", { method: "POST" });
  const data = await r.json();
  if (r.ok && data.ok) { el.innerHTML = '<span style="color:var(--ok)">Задача удалена.</span>'; }
  else { el.innerHTML = '<span class="err-text">Ошибка: ' + esc(data.error || r.status) + '</span>'; }
  loadHopTask();
}
async function runHopNow() {
  const st = document.getElementById("hopStatus");
  const r = await fetch("/api/hoprun", { method: "POST" });
  const data = await r.json();
  if (!r.ok) { st.innerHTML = '<span class="err-text">' + esc(data.error || r.status) + '</span>'; return; }
  st.textContent = "Проверка по узлам выполняется… (смотрите «Журнал по узлам»)";
  const poll = setInterval(async () => {
    const s = await (await fetch("/api/hopstatus")).json();
    if (!s.running) {
      clearInterval(poll);
      st.innerHTML = '<span style="color:var(--ok)">Готово, запись добавлена.</span>';
      if (document.getElementById("logSel").value === "hops") load();
    }
  }, 5000);
}

function saveUiState() {
  try {
    localStorage.setItem("nd_ui", JSON.stringify({
      auto: document.getElementById("auto").checked,
      onlyErr: document.getElementById("onlyErr").checked,
      log: document.getElementById("logSel").value
    }));
  } catch (e) {}
}

function loadUiState() {
  try {
    const s = JSON.parse(localStorage.getItem("nd_ui") || "{}");
    if (s.auto) document.getElementById("auto").checked = true;
    if (s.onlyErr) document.getElementById("onlyErr").checked = true;
    if (s.log) document.getElementById("logSel").value = s.log;
  } catch (e) {}
}

document.getElementById("onlyErr").addEventListener("change", () => { render(); saveUiState(); });
document.getElementById("auto").addEventListener("change", saveUiState);
document.getElementById("logSel").addEventListener("change", saveUiState);
setInterval(() => { if (document.getElementById("auto").checked) load(); }, 60000);
loadUiState();
load();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- сервер

class DashboardServer(ThreadingHTTPServer):
    # SO_REUSEADDR несовместим с SO_EXCLUSIVEADDRUSE на Windows -
    # выключаем reuse, иначе двойной биндинг порта проходит молча.
    allow_reuse_address = False

    def server_bind(self):
        # запрет двойного биндинга порта на Windows (без этого второй
        # экземпляр молча разделяет порт и отдаёт свои пустые данные)
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, ctype, data):
        blob = data.encode("utf-8") if isinstance(data, str) else data
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _request_allowed(self):
        """Пропускать только запросы со страницы самого дашборда.

        Host отсекает DNS rebinding, Origin - CSRF с чужих сайтов
        (браузер всегда шлёт Origin в межсайтовых POST)."""
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in ALLOWED_ORIGINS:
            return False
        return True

    def _forbidden(self):
        self._send(403, "text/plain; charset=utf-8", "forbidden")

    def _json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def do_GET(self):
        if not self._request_allowed():
            self._forbidden()
            return
        if self.path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE)
        elif self.path == "/api/entries":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(entries_payload(MD_LOG), ensure_ascii=False))
        elif self.path == "/api/hops":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(entries_payload(HOP_LOG), ensure_ascii=False))
        elif self.path == "/api/hopstatus":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"running": HOP_STATE["running"]}))
        elif self.path == "/api/config":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(read_config(), ensure_ascii=False))
        elif self.path == "/api/task":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(task_query(), ensure_ascii=False))
        elif self.path == "/api/hoptask":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(task_query(HOP_TASK_NAME), ensure_ascii=False))
        elif self.path == "/favicon.ico":
            if os.path.exists(FAVICON):
                with open(FAVICON, "rb") as fh:
                    self._send(200, "image/x-icon", fh.read())
            else:
                self._send(404, "text/plain; charset=utf-8", "not found")
        else:
            self._send(404, "text/plain; charset=utf-8", "not found")

    def do_POST(self):
        if not self._request_allowed():
            self._forbidden()
            return
        if self.path == "/api/run":
            if not RUN_LOCK.acquire(blocking=False):
                self._send(409, "application/json; charset=utf-8",
                           json.dumps({"error": "Проверка уже выполняется"}))
                return
            RUN_STATE["running"] = True
            try:
                proc = subprocess.run([WRAPPER], cwd=BASE_DIR, capture_output=True,
                                      timeout=900, creationflags=NO_WINDOW)
                self._send(200, "application/json; charset=utf-8",
                           json.dumps({"rc": proc.returncode}))
            except Exception as exc:
                self._send(500, "application/json; charset=utf-8",
                           json.dumps({"error": str(exc)}))
            finally:
                RUN_STATE["running"] = False
                RUN_LOCK.release()
        elif self.path == "/api/hoprun":
            if hop_run_async():
                self._send(200, "application/json; charset=utf-8", json.dumps({"started": True}))
            else:
                self._send(409, "application/json; charset=utf-8",
                           json.dumps({"error": "Проверка по узлам уже выполняется"}))
        elif self.path == "/api/config":
            cfg, err = validate_config(self._json_body())
            if err:
                self._send(400, "application/json; charset=utf-8", json.dumps({"error": err}))
                return
            try:
                write_config(cfg)
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True}))
            except Exception as exc:
                self._send(500, "application/json; charset=utf-8",
                           json.dumps({"error": str(exc)}))
        elif self.path == "/api/task/install":
            body = self._json_body() or {}
            try:
                minutes = int(body.get("minutes", 60))
            except (TypeError, ValueError):
                minutes = 60
            rc, out = task_install(minutes)
            if rc == 0:
                self._send(200, "application/json; charset=utf-8",
                           json.dumps({"ok": True, "task": task_query()}, ensure_ascii=False))
            else:
                self._send(500, "application/json; charset=utf-8",
                           json.dumps({"error": schtasks_error(rc, out)}, ensure_ascii=False))
        elif self.path == "/api/task/delete":
            rc, out = task_delete()
            if rc == 0:
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True}))
            else:
                self._send(500, "application/json; charset=utf-8",
                           json.dumps({"error": schtasks_error(rc, out)}, ensure_ascii=False))
        elif self.path == "/api/hoptask/install":
            body = self._json_body() or {}
            try:
                minutes = int(body.get("minutes", 30))
            except (TypeError, ValueError):
                minutes = 30
            rc, out = hop_task_install(minutes)
            if rc == 0:
                self._send(200, "application/json; charset=utf-8",
                           json.dumps({"ok": True, "task": task_query(HOP_TASK_NAME)}, ensure_ascii=False))
            else:
                self._send(500, "application/json; charset=utf-8",
                           json.dumps({"error": schtasks_error(rc, out)}, ensure_ascii=False))
        elif self.path == "/api/hoptask/delete":
            rc, out = hop_task_delete()
            if rc == 0:
                self._send(200, "application/json; charset=utf-8", json.dumps({"ok": True}))
            else:
                self._send(500, "application/json; charset=utf-8",
                           json.dumps({"error": schtasks_error(rc, out)}, ensure_ascii=False))
        else:
            self._send(404, "text/plain; charset=utf-8", "not found")


def log_line(msg):
    with open(VIEWER_LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def main():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        log_line(traceback.format_exc())
    if not os.path.exists(CONFIG):
        try:
            write_config(read_config())
        except Exception:
            log_line(traceback.format_exc())
    try:
        server = DashboardServer((HOST, PORT), Handler)
    except Exception:
        log_line("Не удалось занять порт %s:%d - вероятно, дашборд уже запущен.\n%s"
                 % (HOST, PORT, traceback.format_exc()))
        raise
    log_line("Старт дашборда: http://%s:%d/ (%s)" % (HOST, PORT, os.path.basename(__file__)))
    try:
        server.serve_forever()
    except Exception:
        log_line(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
