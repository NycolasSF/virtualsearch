"""Helper compartilhado do VirtualSearch.

Abstrai a conexao com o navegador em TRES modos (escolhidos via --mode):

  - fresh   : Chromium novo a cada run (headless). Sem cookies/login. Permite
              N instancias paralelas sem conflito (cada Python = seu Chromium).
  - profile : clone-on-start de um perfil persistente proprio da skill. A cada
              run copia .profile-base/ para um temp, usa, e deleta no fim.
              Preserva login entre runs E permite paralelismo (cada clone tem
              path proprio). Overhead: ~1-2s de copia + disco temporario.
  - cdp     : conecta num Edge ja aberto via CDP (opt-in). Util quando o
              usuario ja navegou manualmente ate o alvo. Limite: serial (1 Edge).

A skill e auto-contida: NAO compartilha profile/diretorio com hotmart-recorder
nem com outras skills. Tudo fica dentro de F:/claude-projetos/skills/virtualsearch/.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

# ---- paths ----

SKILL_ROOT = Path(__file__).resolve().parent
PROFILE_BASE = SKILL_ROOT / ".profile-base"
# Pasta default da skill: tudo que rodar SEM --dest cai aqui (em subpasta auto-gerada).
# Resolucao: env VSEARCH_LIBRARY_ROOT > path canonico do hub (mundo `acervo` do
# cosmos, so existe na maquina Windows do hub) > ~/virtualsearch-library (Linux/mac
# ou maquina nova). A raiz library/ era legado e foi aposentada em 2026-06-01.
_HUB_LIBRARY = Path(r"F:\claude-projetos\_acervo\library")
LIBRARY_ROOT = (
    Path(os.environ["VSEARCH_LIBRARY_ROOT"]) if os.environ.get("VSEARCH_LIBRARY_ROOT")
    else _HUB_LIBRARY if _HUB_LIBRARY.parent.exists()
    else Path.home() / "virtualsearch-library"
)
# Sub-area onde os auxiliares antigos (output_subdir) escreviam — mantida pra retro-compat.
LIBRARY_LEGACY_ROOT = LIBRARY_ROOT / "virtualsearch"

# ---- CDP ----

DEFAULT_CDP_URL = "http://127.0.0.1:9224"


class CDPNotAvailable(RuntimeError):
    """Edge nao esta exposto via CDP."""


def _connect_cdp(pw: Playwright, url: str = DEFAULT_CDP_URL) -> tuple[Browser, BrowserContext]:
    try:
        browser = pw.chromium.connect_over_cdp(url)
    except Exception as exc:
        raise CDPNotAvailable(
            f"Nao foi possivel conectar ao Edge em {url}. "
            "Abra um Chromium/Edge com --remote-debugging-port=9224 ou "
            "use --mode fresh/profile para rodar de forma independente.\n"
            f"Detalhe: {exc}"
        ) from exc

    if not browser.contexts:
        raise CDPNotAvailable(
            "CDP conectado mas sem contexto ativo. Abra uma aba e tente de novo."
        )
    return browser, browser.contexts[0]


# ---- filename helpers ----

_WIN_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(text: str, max_len: int = 120) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _WIN_FORBIDDEN.sub("-", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    text = text.strip("-.").lower()
    return text[:max_len] or "untitled"


def domain_slug(url: str) -> str:
    netloc = urlparse(url).netloc or "unknown"
    return sanitize_filename(netloc, max_len=60)


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def output_subdir(kind: str) -> Path:
    if kind not in {"screenshots", "images", "text", "viewsource"}:
        raise ValueError(f"kind invalido: {kind}")
    return ensure_dir(LIBRARY_LEGACY_ROOT / kind)


# ---- profile clone ----

def _clone_profile_base() -> Path:
    """Copia .profile-base/ para um temp unique e retorna o path temp."""
    if not PROFILE_BASE.exists():
        PROFILE_BASE.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.gettempdir()) / "virtualsearch-clones"
    temp_root.mkdir(parents=True, exist_ok=True)
    clone = temp_root / f"clone-{os.getpid()}-{int(time.time()*1000)}"
    shutil.copytree(PROFILE_BASE, clone, dirs_exist_ok=True)
    return clone


def _rm_tree_safe(path: Path) -> None:
    if not path.exists():
        return
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            time.sleep(0.5 * (attempt + 1))
    # ultimo recurso: deixa pra limpeza manual
    pass


# ---- session context manager ----

@contextmanager
def browser_session(
    mode: str = "profile",
    headed: bool = False,
    keep_profile: bool = False,
    url: str | None = None,
    new_tab: bool = False,
    cdp_url: str = DEFAULT_CDP_URL,
    record_video_dir: str | Path | None = None,
    record_video_size: tuple[int, int] | None = None,
    viewport_size: tuple[int, int] | None = None,
) -> Iterator[tuple[Page, BrowserContext]]:
    """Context manager unificado.

    Args:
        mode: fresh | profile | cdp
        headed: roda com janela visivel. Util pra login inicial (profile) ou debug.
        keep_profile: SOMENTE com mode=profile. Usa PROFILE_BASE direto em vez de
                      clonar - persiste cookies/login entre runs. Incompativel com
                      paralelismo. Use uma vez pra logar, depois rode sem a flag.
        url: se passado, navega pra ele. Se None, usa aba ativa (cdp) ou nova.
        new_tab: em modo cdp, abre aba nova em vez de reusar a ativa.
        cdp_url: endereco do CDP (so modo cdp).
        record_video_dir: liga screen-recording do Playwright (frames+audio do
                          viewport como WebM). INCOMPATIVEL com mode=cdp (CDP nao
                          suporta record_video). O .webm gerado e renomeado pelo
                          chamador apos o context fechar.
        record_video_size: (width, height) do video gravado. Default Playwright = viewport.
        viewport_size: (width, height) do viewport. Util quando se quer captura
                       em resolucao especifica (ex: 1920x1080).

    Yields:
        (page, context) prontos pra uso. Context tem .request pra downloads
        autenticados. Se record_video_dir foi passado, page.video tem .path()
        APOS context.close() (Playwright nao expoe enquanto roda).
    """
    if mode not in {"fresh", "profile", "cdp"}:
        raise ValueError(f"mode invalido: {mode} (use fresh|profile|cdp)")
    if keep_profile and mode != "profile":
        raise ValueError("--keep-profile so faz sentido com --mode profile")
    if record_video_dir is not None and mode == "cdp":
        raise ValueError(
            "record_video_dir incompativel com mode=cdp (CDP nao suporta "
            "record_video). Use --mode fresh ou profile."
        )

    pw = sync_playwright().start()
    browser: Browser | None = None
    context: BrowserContext | None = None
    temp_profile: Path | None = None

    if record_video_dir is not None:
        record_video_dir = str(Path(record_video_dir).resolve())
        Path(record_video_dir).mkdir(parents=True, exist_ok=True)

    context_kwargs: dict = {}
    if record_video_dir is not None:
        context_kwargs["record_video_dir"] = record_video_dir
        if record_video_size is not None:
            context_kwargs["record_video_size"] = {
                "width": record_video_size[0],
                "height": record_video_size[1],
            }
    if viewport_size is not None:
        context_kwargs["viewport"] = {
            "width": viewport_size[0],
            "height": viewport_size[1],
        }

    try:
        if mode == "cdp":
            browser, context = _connect_cdp(pw, cdp_url)
            if url is None:
                if not context.pages:
                    raise RuntimeError("CDP sem abas. Abra uma antes de rodar.")
                page = context.pages[0]
            elif new_tab:
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            else:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

        elif mode == "fresh":
            browser = pw.chromium.launch(headless=not headed)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

        else:  # profile
            if keep_profile:
                profile_path = PROFILE_BASE
                ensure_dir(profile_path)
            else:
                temp_profile = _clone_profile_base()
                profile_path = temp_profile

            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=not headed,
                **context_kwargs,
            )
            # launch_persistent_context ja traz 1 page por default
            page = context.pages[0] if context.pages else context.new_page()
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # networkidle best-effort (sites com heartbeat nunca ficam idle)
        if url:
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

        yield page, context

    finally:
        try:
            if mode == "cdp":
                # CDP: so desanexa, nao fecha (e o Edge do usuario)
                if browser is not None:
                    browser.close()  # close() no handle CDP apenas desconecta
            elif mode == "fresh":
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()
            else:  # profile
                if context is not None:
                    context.close()
        except Exception:
            pass

        try:
            pw.stop()
        except Exception:
            pass

        if temp_profile is not None:
            _rm_tree_safe(temp_profile)


# ---- compat: mantido para cenarios que ainda queiram CDP direto ----

def connect_cdp(url: str = DEFAULT_CDP_URL) -> tuple[Playwright, Browser, BrowserContext]:
    """API antiga: conecta no CDP e devolve (pw, browser, context).

    Preferencia: usar `browser_session(mode='cdp')`. Mantido por compatibilidade.
    """
    pw = sync_playwright().start()
    try:
        browser, context = _connect_cdp(pw, url)
    except Exception:
        pw.stop()
        raise
    return pw, browser, context


def get_active_page(context: BrowserContext, url: str | None = None, new_tab: bool = False) -> Page:
    """API antiga. Preferencia: `browser_session` cuida disso."""
    if url is None:
        if not context.pages:
            raise RuntimeError("Sem abas ativas.")
        return context.pages[0]
    page = context.new_page() if new_tab else (context.pages[0] if context.pages else context.new_page())
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    return page
