"""Roda os checks da VirtualSearch e atualiza STATUS.md.

Uso:
  # Roda todos os checks automaticos (deps + smoke + paralelismo)
  python check_status.py

  # Marca um check manual como passou/falhou (gaps, modos com login)
  python check_status.py --mark T11 pass "testei com Hotmart, login ok"
  python check_status.py --mark G01 fail "User-Agent ainda n\xe3o implementado"

  # So mostra status atual (nao executa)
  python check_status.py --show
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

SKILL_ROOT = Path(__file__).resolve().parent
STATUS_MD = SKILL_ROOT / "STATUS.md"
STATE_JSON = SKILL_ROOT / ".status-state.json"
PROFILE_BASE = SKILL_ROOT / ".profile-base"
_TEST_DEST = SKILL_ROOT / ".test-output"  # destino temp pros smoke tests

AUTO_START = "<!-- AUTO:START -->"
AUTO_END = "<!-- AUTO:END -->"


# ---- registry ----

@dataclass
class Check:
    id: str
    title: str
    category: str
    kind: str  # "auto" | "manual"
    runner: Callable[[], tuple[str, str]] | None = None  # retorna (status, note)


def _dep_check(module: str) -> tuple[str, str]:
    spec = importlib.util.find_spec(module)
    if spec is None:
        return "fail", f"ModuleNotFoundError: {module}"
    return "pass", f"{module} importavel"


def _run_script(args: list[str], expected_output_substr: str | None = None, timeout: int = 60) -> tuple[str, str]:
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, *args],
            cwd=str(SKILL_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return "fail", f"timeout ({timeout}s)"
    elapsed = time.time() - t0
    last_line = (proc.stdout.strip().splitlines() or [""])[-1]
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip().splitlines()
        tail = err[-1] if err else "no output"
        return "fail", f"exit={proc.returncode} | {tail[:100]}"
    if expected_output_substr and expected_output_substr not in proc.stdout:
        return "fail", f"saida nao contem '{expected_output_substr}'"
    return "pass", f"{elapsed:.1f}s | {last_line[:80]}"


def _check_playwright_chromium() -> tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        return "fail", f"playwright nao importa: {e}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            browser.close()
        return "pass", "chromium launch ok"
    except Exception as e:
        return "fail", f"{type(e).__name__}: {str(e)[:100]}"


def _check_profile_base() -> tuple[str, str]:
    if PROFILE_BASE.exists() and any(PROFILE_BASE.iterdir()):
        return "pass", f".profile-base populado"
    return "skip", ".profile-base vazio (rode --headed --keep-profile pra logar)"


def _check_ffmpeg() -> tuple[str, str]:
    """ffmpeg eh dependencia opcional do record_video.py (concat de re-arms)."""
    import shutil as _sh
    path = _sh.which("ffmpeg")
    if path:
        return "pass", f"ffmpeg em {path}"
    return "skip", "ffmpeg ausente (record_video usa fallback binario)"


def _check_record_video_help() -> tuple[str, str]:
    """Smoke: record_video.py --help responde (modulo importa, argparse OK)."""
    proc = subprocess.run(
        [sys.executable, "record_video.py", "--help"],
        cwd=str(SKILL_ROOT),
        capture_output=True, text=True, timeout=15,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return "fail", f"exit={proc.returncode} | {proc.stderr.strip()[:80]}"
    if "--dest" not in proc.stdout or "--iframe-selector" not in proc.stdout:
        return "fail", "help nao contem flags esperadas"
    return "pass", "help responde com flags esperadas"


def _check_record_video_dest_required() -> tuple[str, str]:
    """record_video.py sem --dest deve falhar com exit !=0."""
    proc = subprocess.run(
        [sys.executable, "record_video.py", "--url", "https://example.com", "--mode", "fresh"],
        cwd=str(SKILL_ROOT),
        capture_output=True, text=True, timeout=15,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode == 0:
        return "fail", "rodou sem --dest"
    if "--dest" in (proc.stderr + proc.stdout):
        return "pass", f"falhou corretamente (exit={proc.returncode})"
    return "fail", f"falhou mas sem mencionar --dest: {proc.stderr[:80]}"


def _check_requests_importable() -> tuple[str, str]:
    spec = importlib.util.find_spec("requests")
    if spec is None:
        return "fail", "requests nao importavel — pip install requests"
    return "pass", "requests importavel"


def _check_transcribe_helper_imports() -> tuple[str, str]:
    """transcribe_helper deve importar e expor is_audio_agent_up + transcribe_to_txt."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "from transcribe_helper import is_audio_agent_up, transcribe_to_txt; print('ok')"],
        cwd=str(SKILL_ROOT),
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return "fail", f"import falhou: {proc.stderr.strip()[:80]}"
    return "pass", "is_audio_agent_up + transcribe_to_txt importaveis"


def _check_win_notify_imports() -> tuple[str, str]:
    proc = subprocess.run(
        [sys.executable, "-c", "from win_notify import notify; print('ok')"],
        cwd=str(SKILL_ROOT),
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return "fail", f"import falhou: {proc.stderr.strip()[:80]}"
    return "pass", "win_notify.notify importavel"


def _check_setup_login_help() -> tuple[str, str]:
    proc = subprocess.run(
        [sys.executable, "setup_login.py", "--help"],
        cwd=str(SKILL_ROOT),
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return "fail", f"exit={proc.returncode}"
    if "--wait-selector" not in proc.stdout or "--url" not in proc.stdout:
        return "fail", "help nao contem flags esperadas"
    return "pass", "help responde com flags esperadas"


def _check_batch_record_help() -> tuple[str, str]:
    proc = subprocess.run(
        [sys.executable, "batch_record.py", "--help"],
        cwd=str(SKILL_ROOT),
        capture_output=True, text=True, timeout=10,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return "fail", f"exit={proc.returncode}"
    if "--urls" not in proc.stdout or "--no-skip-list" not in proc.stdout:
        return "fail", "help nao contem flags esperadas"
    return "pass", "help responde com flags esperadas"


def _check_audio_agent_up() -> tuple[str, str]:
    """Opcional: ve se audio-agent esta no ar pra --transcribe funcionar."""
    try:
        from transcribe_helper import is_audio_agent_up
    except Exception as e:
        return "fail", f"import falhou: {e}"
    if is_audio_agent_up():
        return "pass", "audio-agent online em localhost:8020"
    return "skip", "audio-agent offline (subir com: cd audio-agent && python main.py)"


def _check_parallel_fresh() -> tuple[str, str]:
    """Dispara 2 screenshots em paralelo em modo fresh. Falha se um travar o outro."""
    t0 = time.time()
    procs = [
        subprocess.Popen(
            [sys.executable, "screenshot_page.py",
             "--dest", str(_TEST_DEST / f"parallel-{i}"),
             "--url", "https://example.com", "--mode", "fresh"],
            cwd=str(SKILL_ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for i in range(2)
    ]
    results = [p.wait(timeout=60) for p in procs]
    elapsed = time.time() - t0
    if all(r == 0 for r in results):
        return "pass", f"2x paralelo fresh em {elapsed:.1f}s"
    return "fail", f"exit codes: {results}"


def _check_dest_required() -> tuple[str, str]:
    """Confirma que rodar sem --dest falha com exit=2 e mensagem clara."""
    proc = subprocess.run(
        [sys.executable, "screenshot_page.py", "--url", "https://example.com", "--mode", "fresh"],
        cwd=str(SKILL_ROOT),
        capture_output=True, text=True, timeout=15,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode == 0:
        return "fail", "rodou sem --dest (nao falhou)"
    if "--dest" in (proc.stderr + proc.stdout):
        return "pass", f"falhou corretamente (exit={proc.returncode})"
    return "fail", f"falhou mas sem mencionar --dest: {proc.stderr[:80]}"


CHECKS: list[Check] = [
    # Deps
    Check("D01", "playwright importavel", "Dependencias", "auto", lambda: _dep_check("playwright")),
    Check("D02", "readability-lxml importavel", "Dependencias", "auto", lambda: _dep_check("readability")),
    Check("D03", "markdownify importavel", "Dependencias", "auto", lambda: _dep_check("markdownify")),
    Check("D04", "Chromium instalado (playwright)", "Dependencias", "auto", _check_playwright_chromium),
    Check("D05", "ffmpeg no PATH (record_video opcional)", "Dependencias", "auto", _check_ffmpeg),
    Check("D06", "requests importavel (transcribe_helper)", "Dependencias", "auto", _check_requests_importable),

    # Smoke tests (modo fresh, sites publicos). Cada um usa um subdir temp.
    Check("T01", "screenshot_page --mode fresh", "Smoke tests", "auto",
          lambda: _run_script(["screenshot_page.py", "--dest", str(_TEST_DEST / "t01"), "--url", "https://example.com", "--mode", "fresh"], "[OK]")),
    Check("T02", "scrape_text --mode fresh (readability)", "Smoke tests", "auto",
          lambda: _run_script(["scrape_text.py", "--dest", str(_TEST_DEST / "t02"), "--url", "https://example.com", "--mode", "fresh", "--format", "md"], "[OK]")),
    Check("T03", "scrape_images --mode fresh", "Smoke tests", "auto",
          lambda: _run_script(["scrape_images.py", "--dest", str(_TEST_DEST / "t03"), "--url", "https://picsum.photos/", "--mode", "fresh", "--min-size", "0"], "[OK]", timeout=90)),
    Check("T04", "scrape_viewsource --format html", "Smoke tests", "auto",
          lambda: _run_script(["scrape_viewsource.py", "--dest", str(_TEST_DEST / "t04"), "--url", "https://example.com", "--mode", "fresh", "--format", "html"], "[OK]")),
    Check("T05", "scrape_viewsource --format md", "Smoke tests", "auto",
          lambda: _run_script(["scrape_viewsource.py", "--dest", str(_TEST_DEST / "t05"), "--url", "https://example.com", "--mode", "fresh", "--format", "md"], "[OK]")),
    Check("T06", "--dest obrigatorio (falha sem --dest)", "Smoke tests", "auto",
          lambda: _check_dest_required()),
    Check("T07", "record_video.py --help responde",
          "Smoke tests", "auto", _check_record_video_help),
    Check("T08", "record_video.py --dest obrigatorio",
          "Smoke tests", "auto", _check_record_video_dest_required),
    Check("T09", "transcribe_helper imports OK",
          "Smoke tests", "auto", _check_transcribe_helper_imports),
    Check("T15", "win_notify import OK",
          "Smoke tests", "auto", _check_win_notify_imports),
    Check("T16", "setup_login.py --help responde",
          "Smoke tests", "auto", _check_setup_login_help),
    Check("T17", "batch_record.py --help responde",
          "Smoke tests", "auto", _check_batch_record_help),
    Check("T18", "audio-agent online em :8020 (opcional)",
          "Smoke tests", "auto", _check_audio_agent_up),

    # Modos avancados
    Check("T10", ".profile-base existe e esta populado", "Modos", "auto", _check_profile_base),
    Check("T11", "--mode profile em site gated (manual)", "Modos", "manual"),
    Check("T12", "--mode cdp conecta em Edge :9224 (manual, requer Edge aberto)", "Modos", "manual"),
    Check("T13", "record_video.py em site real com video (manual, exige login)", "Modos", "manual"),
    Check("T14", "setup_login.py popula .profile-base em site real (manual)", "Modos", "manual"),
    Check("T19", "batch_record.py em site real com 2+ URLs (manual)", "Modos", "manual"),
    Check("T20", "record_video.py --with-video viewport.webm em player sem DRM (manual)", "Modos", "manual"),

    # Paralelismo
    Check("P01", "2x --mode fresh paralelo sem conflito", "Paralelismo", "auto", _check_parallel_fresh),
    Check("P02", "2x --mode profile paralelo com clones distintos (manual)", "Paralelismo", "manual"),

    # Gaps conhecidos (trackear nao-implementado)
    Check("G01", "User-Agent customizado (--user-agent flag)", "Gaps / Melhorias", "manual"),
    Check("G02", "Scroll automatico em scrape_images (lazy-load)", "Gaps / Melhorias", "manual"),
    Check("G03", "Retry em falhas de rede", "Gaps / Melhorias", "manual"),
    Check("G04", "Timeout de goto configuravel via flag", "Gaps / Melhorias", "manual"),
    Check("G05", "Bypass anti-copy validado em site real com user-select:none", "Gaps / Melhorias", "manual"),
    Check("G06", "record_video: validar dual-watchdog em player que reconstrua MediaStream em campo", "Gaps / Melhorias", "manual"),
]


# ---- state ----

def load_state() -> dict:
    if STATE_JSON.exists():
        try:
            return json.loads(STATE_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_JSON.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ---- render ----

MARK = {"pass": "[x]", "fail": "[!]", "skip": "[~]", "pending": "[ ]"}


def render_table(state: dict) -> str:
    lines = []
    categories = {}
    for c in CHECKS:
        categories.setdefault(c.category, []).append(c)

    for cat, items in categories.items():
        lines.append(f"### {cat}\n")
        lines.append("| ID | Status | Check | Ultima execucao | Nota |")
        lines.append("|---|---|---|---|---|")
        for c in items:
            entry = state.get(c.id, {})
            status = entry.get("status", "pending")
            mark = MARK.get(status, "[?]")
            ts = entry.get("ts", "-")
            note = (entry.get("note") or "").replace("|", "\\|")[:80]
            kind_badge = "auto" if c.kind == "auto" else "manual"
            lines.append(f"| {c.id} | {mark} `{status}` | {c.title} _({kind_badge})_ | {ts} | {note} |")
        lines.append("")
    return "\n".join(lines)


def render_summary(state: dict) -> str:
    total = len(CHECKS)
    by_status = {"pass": 0, "fail": 0, "skip": 0, "pending": 0}
    for c in CHECKS:
        st = state.get(c.id, {}).get("status", "pending")
        by_status[st] = by_status.get(st, 0) + 1
    pct = (by_status["pass"] / total) * 100 if total else 0
    return (
        f"**Resumo:** {by_status['pass']}/{total} passando ({pct:.0f}%) | "
        f"falhas: {by_status['fail']} | pulados: {by_status['skip']} | "
        f"pendentes: {by_status['pending']}\n"
    )


STATIC_INTRO = """# VirtualSearch — Status & Checklist

Arquivo **auto-atualizado** por `check_status.py`. Roda os checks automaticos (deps, smoke, paralelismo) e agrega status dos checks manuais (modos com login, gaps conhecidos).

## Como usar

```bash
# Rodar todos os checks automaticos
python check_status.py

# Marcar um check manual (apos testar na mao)
python check_status.py --mark T11 pass "testado no Hotmart, login ok"
python check_status.py --mark G01 fail "ainda nao implementado"

# Ver status sem rodar nada
python check_status.py --show
```

Legenda: `[x]` pass | `[!]` fail | `[~]` skip (condicao nao atendida) | `[ ]` pending
"""

STATIC_FOOTER = """
## Gaps conhecidos — detalhes

**G01 — User-Agent customizado.** Alguns sites (Wikipedia, sites com WAF) devolvem HTTP 429 pra User-Agent de bot. A skill usa o default do Chromium/Playwright. Solucao: adicionar flag `--user-agent` em `browser_common.py` passando pro `new_context(user_agent=...)`.

**G02 — Scroll automatico em `scrape_images`.** Paginas com lazy-load so carregam imagens conforme voce rola. Hoje a skill so pega o DOM inicial. Solucao: adicionar `--scroll` que roda `page.evaluate("window.scrollTo(0, document.body.scrollHeight)")` em loop ate altura parar de crescer.

**G03 — Retry em falhas de rede.** Se um download falha por glitch de rede, erro direto. Pra batch grande isso dói. Solucao: envolver `context.request.get()` em retry com backoff (tenacity ou manual).

**G04 — Timeout de `goto` configuravel.** Hoje fixo em 30s dentro de `browser_common.py:browser_session`. Sites lentos (SaaS pesado, paywalls com redirect) podem falhar. Solucao: adicionar flag `--goto-timeout <ms>`.

**G05 — Bypass anti-copy.** Testado em site permissivo (`example.com`), mas nao validado num site real com `user-select: none` + bloqueio de right-click + listener de `copy`. Encontrar caso real e validar.

**T11 — `--mode profile` em site gated.** Precisa rodar `--headed --keep-profile` uma vez no site alvo (Hotmart, SaaS) pra popular `.profile-base/`, depois validar que runs subsequentes sem `--headed` herdam o login via clone.

**T12 — `--mode cdp`.** Precisa Chromium/Edge aberto em `127.0.0.1:9224`. Testar apontando pro Edge ja logado e confirmar que reusa a aba ativa.

**P02 — Paralelismo em `--mode profile`.** Disparar 2 scripts ao mesmo tempo em modo profile. Cada um clona pra temp unica, nao deve haver lock de `SingletonLock`. Ver se os dois terminam sem travar.

**T13 — `record_video.py` em site real.** Smoke automatico (T07/T08) so confere import + validacao de flags. O fluxo completo (login -> navegar -> achar `<video>` -> gravar -> concat) precisa ser testado em pelo menos um site real (curso da Hotmart, Vimeo publico, player custom). Validar que taxa MB/min >0.5, que watchdog Python re-arma corretamente em stall artificial (minimizar a janela) e que o `.webm` final abre no audio-agent sem rejeicao.

**G06 — Dual-watchdog em player que reconstrua MediaStream.** O dual-watchdog foi validado em campo no Hotmart/Orbyka (modulo 6 do Rise gravado em 2026-04-20). Em outros players (Vimeo, JW, Brightcove, custom HLS) ainda nao foi exercitado. Quando aparecer caso real, registrar comportamento aqui.
"""


def write_md(state: dict) -> None:
    summary = render_summary(state)
    table = render_table(state)
    auto_block = f"{AUTO_START}\n\n_Gerado em {datetime.now().isoformat(timespec='seconds')}_\n\n{summary}\n{table}\n{AUTO_END}"
    content = f"{STATIC_INTRO}\n{auto_block}\n{STATIC_FOOTER}"
    STATUS_MD.write_text(content, encoding="utf-8")


# ---- run ----

def run_auto_checks() -> dict:
    state = load_state()
    print(f"[check_status] rodando {sum(1 for c in CHECKS if c.kind=='auto')} checks automaticos...\n")
    for c in CHECKS:
        if c.kind != "auto" or c.runner is None:
            continue
        print(f"  {c.id} {c.title} ...", end=" ", flush=True)
        try:
            status, note = c.runner()
        except Exception as e:
            status, note = "fail", f"{type(e).__name__}: {str(e)[:80]}"
        ts = datetime.now().isoformat(timespec="seconds")
        state[c.id] = {"status": status, "note": note, "ts": ts}
        print(f"{MARK[status]} {note}")
    save_state(state)
    write_md(state)
    return state


def mark_manual(check_id: str, status: str, note: str = "") -> None:
    valid = {"pass", "fail", "skip", "pending"}
    if status not in valid:
        print(f"[ERRO] status invalido: {status} (use: {valid})", file=sys.stderr)
        sys.exit(2)
    if not any(c.id == check_id for c in CHECKS):
        print(f"[ERRO] check id desconhecido: {check_id}", file=sys.stderr)
        sys.exit(2)
    state = load_state()
    state[check_id] = {
        "status": status,
        "note": note,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    save_state(state)
    write_md(state)
    print(f"[OK] {check_id} = {status} | {note}")


def show_status() -> None:
    state = load_state()
    print(render_summary(state))
    print(render_table(state))


# ---- cli ----

def main() -> int:
    p = argparse.ArgumentParser(description="VirtualSearch status checker.")
    p.add_argument("--mark", nargs=3, metavar=("ID", "STATUS", "NOTE"),
                   help="Marca check manual. Ex: --mark T11 pass 'Hotmart ok'")
    p.add_argument("--show", action="store_true", help="So exibe status atual.")
    args = p.parse_args()

    if args.mark:
        mark_manual(args.mark[0], args.mark[1], args.mark[2])
        return 0
    if args.show:
        show_status()
        return 0
    state = run_auto_checks()
    total = len(CHECKS)
    passed = sum(1 for c in CHECKS if state.get(c.id, {}).get("status") == "pass")
    print(f"\n[done] STATUS.md atualizado. {passed}/{total} checks passando.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
