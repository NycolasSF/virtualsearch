"""Registro de execucao — gera register.md em tempo real na pasta de destino.

REGRA DA SKILL: TODA execucao DEVE criar um plano de mapeamento (lista de
passos com `plan()`) e atualizar o progresso a cada acao concluida (via
`start()`, `complete()`, `fail()`, `skip()`, `note()`). O `register.md` e
reescrito a cada chamada (flush sincrono) — o usuario pode abrir o arquivo a
qualquer momento e ver onde a execucao esta. Nao existe execucao silenciosa.

Cada script do VirtualSearch instancia um ExecutionRegister no inicio, declara
seus passos com `plan()`, e chama `complete()` conforme avanca. O arquivo e
regravado apos cada mudanca, entao o usuario pode abri-lo em qualquer momento
pra ver o progresso vivo.

Estrutura do register.md:

    # VirtualSearch — Registro de execucao

    **Script:** scrape_images.py
    **URL:** https://...
    **Destino:** F:/alguma/pasta
    **Modo:** profile
    **Iniciado em:** 2026-04-21T00:30:12
    **Status:** em progresso | concluido | falhou

    ## Passos

    - [x] Conectar browser (profile clone)
    - [x] Navegar para URL
    - [>] Baixar 24 imagens   ← em progresso
    - [ ] Cleanup

    ## Resultado

    (preenchido no finish)

    ## Log

    - 00:30:12 — inicio
    - 00:30:13 — browser conectado
    - ...
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# Pasta default da skill quando o usuario nao passa --dest.
# (Mantida aqui para evitar import circular com browser_common.)
DEFAULT_LIBRARY_ROOT = Path(r"F:\claude-projetos\acervo\library")

MARK_DONE = "[x]"
MARK_TODO = "[ ]"
MARK_ACTIVE = "[>]"
MARK_FAIL = "[!]"


class ExecutionRegister:
    def __init__(
        self,
        dest_dir: str | Path,
        script: str,
        url: str | None,
        mode: str,
        extra_meta: dict | None = None,
    ):
        self.dest = Path(dest_dir)
        self.dest.mkdir(parents=True, exist_ok=True)
        self.path = self.dest / "register.md"
        self.script = script
        self.url = url or "(aba ativa)"
        self.mode = mode
        self.extra_meta = extra_meta or {}
        self.started_at = datetime.now()
        self.status = "em progresso"
        self.steps: list[dict] = []  # [{text, state, note}]
        self.log: list[str] = []
        self.result: str | None = None
        self._log(f"inicio | script={script} | url={self.url} | mode={mode}")
        self.flush()

    def plan(self, steps: list[str]) -> None:
        """Define a lista de passos planejados (todos em [ ])."""
        self.steps = [{"text": s, "state": "todo", "note": ""} for s in steps]
        self._log(f"plano definido com {len(steps)} passos")
        self.flush()

    def add_step(self, text: str) -> int:
        """Anexa um passo adicional (util quando N eh dinamico, ex: por imagem)."""
        self.steps.append({"text": text, "state": "todo", "note": ""})
        idx = len(self.steps) - 1
        self.flush()
        return idx

    def start(self, idx: int) -> None:
        if 0 <= idx < len(self.steps):
            self.steps[idx]["state"] = "active"
            self._log(f"start  | {self.steps[idx]['text']}")
            self.flush()

    def complete(self, idx: int, note: str = "") -> None:
        if 0 <= idx < len(self.steps):
            self.steps[idx]["state"] = "done"
            if note:
                self.steps[idx]["note"] = note
            self._log(f"done   | {self.steps[idx]['text']}" + (f" | {note}" if note else ""))
            self.flush()

    def fail(self, idx: int, note: str = "") -> None:
        if 0 <= idx < len(self.steps):
            self.steps[idx]["state"] = "fail"
            if note:
                self.steps[idx]["note"] = note
            self._log(f"fail   | {self.steps[idx]['text']}" + (f" | {note}" if note else ""))
            self.flush()

    def skip(self, idx: int, note: str = "") -> None:
        if 0 <= idx < len(self.steps):
            self.steps[idx]["state"] = "skip"
            if note:
                self.steps[idx]["note"] = note
            self._log(f"skip   | {self.steps[idx]['text']}" + (f" | {note}" if note else ""))
            self.flush()

    def note(self, msg: str) -> None:
        """Adiciona linha avulsa no Log + flush imediato (uso em progresso/eventos
        que nao mudam o estado de um passo, ex: marcas de 25/50/75% ou re-arms)."""
        self._log(msg)
        self.flush()

    def finish(self, status: str, result_summary: str = "") -> None:
        """status: 'concluido' | 'falhou' | 'parcial'."""
        self.status = status
        self.result = result_summary
        self._log(f"fim    | status={status}")
        self.flush()

    # ---- internos ----

    def _log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"- {stamp} — {msg}")

    def _mark(self, state: str) -> str:
        return {
            "done": MARK_DONE,
            "todo": MARK_TODO,
            "active": MARK_ACTIVE,
            "fail": MARK_FAIL,
            "skip": "[~]",
        }.get(state, "[?]")

    def flush(self) -> None:
        lines = [
            "# VirtualSearch — Registro de execucao",
            "",
            f"**Script:** `{self.script}`  ",
            f"**URL:** {self.url}  ",
            f"**Destino:** `{self.dest}`  ",
            f"**Modo:** `{self.mode}`  ",
            f"**Iniciado em:** {self.started_at.isoformat(timespec='seconds')}  ",
            f"**Status:** {self.status}",
        ]
        if self.extra_meta:
            lines.append("")
            for k, v in self.extra_meta.items():
                lines.append(f"**{k}:** {v}  ")
        lines.append("")
        lines.append("## Passos")
        lines.append("")
        if not self.steps:
            lines.append("_(sem passos ainda — aguardando plano)_")
        else:
            for s in self.steps:
                m = self._mark(s["state"])
                text = s["text"]
                note = f" — _{s['note']}_" if s["note"] else ""
                lines.append(f"- {m} {text}{note}")
        lines.append("")
        if self.result is not None:
            lines.append("## Resultado")
            lines.append("")
            lines.append(self.result if self.result else "_(sem resumo)_")
            lines.append("")
        lines.append("## Log")
        lines.append("")
        lines.extend(self.log)
        lines.append("")

        self.path.write_text("\n".join(lines), encoding="utf-8")


_WIN_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _WIN_FORBIDDEN.sub("-", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    text = text.strip("-.").lower()
    return text[:max_len] or "untitled"


def compute_default_dest(
    url: str | None = None,
    script: str | None = None,
    base: Path | None = None,
) -> Path:
    """Default --dest pra quando o usuario nao passa.

    Retorna F:/claude-projetos/library/ (a raiz). Todos os arquivos gerados
    (PLAN.md, register.md, .webm, .png, ...) caem la diretamente.

    NOTA sobre colisao: PLAN.md e register.md sao reescritos por cada
    execucao que cair no default. Os artefatos com timestamp no nome
    (.webm, .png, .md, .txt) coexistem sem colidir. Para isolar uma
    execucao, passe --dest explicito (ex: --dest F:/library/curso-X/).
    """
    return (base or DEFAULT_LIBRARY_ROOT)


def validate_dest(
    dest: str | None,
    url: str | None = None,
    script: str = "virtualsearch",
) -> Path:
    """Valida --dest. Se None/vazio, usa default F:/claude-projetos/library/.

    Sempre cria a pasta. Retorna Path absoluto.

    Levanta ValueError se dest aponta pra arquivo existente (nao pasta).
    """
    if not dest or not dest.strip():
        p = compute_default_dest(url=url, script=script)
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()
    p = Path(dest).expanduser().resolve()
    if p.exists() and not p.is_dir():
        raise ValueError(f"--dest existe mas nao eh pasta: {p}")
    p.mkdir(parents=True, exist_ok=True)
    return p
