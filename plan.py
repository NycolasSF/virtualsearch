"""Plano de mapeamento + processo de atualizacao.

Cada execucao da skill VirtualSearch cria DOIS arquivos no `--dest`:

  - PLAN.md     -> escrito UMA VEZ no inicio. Descreve o mapeamento da
                   captura (objetivo, escopo, artefatos esperados) e o
                   PROCESSO de atualizacao (como acompanhar progresso, onde
                   olhar, quais marcas significam o que).
  - register.md -> reescrito a CADA passo (responsabilidade do
                   ExecutionRegister em register.py). E o estado vivo.

PLAN.md serve pra responder antes de qualquer tool call:
  - O que essa execucao vai fazer?
  - Onde ficam os arquivos?
  - Como saber se terminou? Como saber se travou?

PLAN.md e estavel. Pode ter `update_plan_md()` chamado em pontos especificos
pra refletir mudancas grandes (ex: usuario passou nova URL no batch), mas
em geral ele nao muda durante a run.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable


PLAN_FILENAME = "PLAN.md"


def _fmt_kv(items: dict | None) -> list[str]:
    if not items:
        return []
    out = []
    for k, v in items.items():
        out.append(f"- **{k}**: {v}")
    return out


def write_plan_md(
    dest: Path,
    *,
    script: str,
    url: str | None,
    mode: str,
    objective: str,
    scope: list[str],
    artifacts: list[str],
    update_process: list[str] | None = None,
    extras: dict | None = None,
) -> Path:
    """Cria/sobrescreve PLAN.md no `dest`.

    Args:
        dest: pasta de destino (ja existente).
        script: nome do script invocador (ex: 'record_video.py').
        url: URL alvo (se aplicavel).
        mode: modo do browser (fresh|profile|cdp).
        objective: 1 frase descrevendo o que a execucao vai entregar.
        scope: bullets do que esta dentro do escopo (e do que NAO esta, se
               relevante).
        artifacts: bullets dos arquivos que serao gerados nessa pasta.
        update_process: bullets descrevendo como o usuario acompanha progresso.
                        Se None, usa o padrao da skill (register.md vivo).
        extras: dict opcional com info contextual (iframe-selector, duracao,
                viewport, etc.).

    Returns:
        Path do PLAN.md escrito.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / PLAN_FILENAME

    if update_process is None:
        update_process = _default_update_process()

    ts = datetime.now().isoformat(timespec="seconds")

    lines: list[str] = [
        "# VirtualSearch — Plano de mapeamento + processo de atualizacao",
        "",
        f"**Script:** `{script}`  ",
        f"**URL:** {url or '(aba ativa / nao aplicavel)'}  ",
        f"**Destino:** `{dest}`  ",
        f"**Modo:** `{mode}`  ",
        f"**Plano gerado em:** {ts}  ",
        "",
        "## Objetivo",
        "",
        objective.strip() or "_(sem objetivo declarado)_",
        "",
        "## Mapeamento (escopo)",
        "",
    ]
    if scope:
        for item in scope:
            lines.append(f"- {item}")
    else:
        lines.append("_(escopo nao declarado)_")

    lines += [
        "",
        "## Artefatos esperados",
        "",
    ]
    if artifacts:
        for item in artifacts:
            lines.append(f"- {item}")
    else:
        lines.append("_(nenhum artefato declarado)_")

    if extras:
        lines += ["", "## Parametros da execucao", ""]
        lines += _fmt_kv(extras)

    lines += [
        "",
        "## Processo de atualizacao de progresso",
        "",
    ]
    for item in update_process:
        lines.append(f"- {item}")

    lines += [
        "",
        "## Como acompanhar em tempo real",
        "",
        f"- Abra `{dest / 'register.md'}` em qualquer editor — e reescrito a cada passo.",
        "- Marcas no checklist:",
        "  - `[ ]` pendente",
        "  - `[>]` em execucao agora",
        "  - `[x]` concluido",
        "  - `[!]` falhou (veja a `_nota_` ao lado)",
        "  - `[~]` pulado",
        "- Cada acao loga uma linha em `## Log` com timestamp `HH:MM:SS`.",
        "- Quando a execucao termina, `## Resultado` ganha resumo final.",
        "",
        "## Como saber se travou",
        "",
        "- O `register.md` deixa de receber novas linhas em `## Log` por mais de ~30s.",
        "- Para gravacao de video, o tamanho do `.webm` para de crescer (compare via `ls -la`).",
        "- Em qualquer caso, abra `PLAN.md` (este arquivo) pra reconfirmar o que era esperado.",
        "",
        "## Apos terminar",
        "",
        "- O `register.md` fica como historico (status: concluido | falhou | parcial).",
        "- Os arquivos gerados ficam nessa mesma pasta.",
        "- Esse `PLAN.md` permanece imutavel — referencia do que foi planejado.",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _default_update_process() -> list[str]:
    return [
        "`register.md` na mesma pasta e reescrito apos CADA passo (flush sincrono).",
        "Cada passo tem 4 estados visiveis no checklist: `[ ]` -> `[>]` -> `[x]` ou `[!]` ou `[~]`.",
        "A secao `## Log` recebe uma linha timestamped (`HH:MM:SS`) por evento.",
        "Eventos pontuais (re-arms, marcas de progresso 25/50/75%, downloads concluidos) viram linhas extras de `note` no log.",
        "Ao terminar, `## Resultado` consolida o que foi gerado e tamanhos.",
    ]


def append_plan_section(
    dest: Path,
    title: str,
    body: str,
) -> Path:
    """Anexa uma secao adicional ao PLAN.md (ex: revisao de escopo no meio do batch).

    Util quando o batch ganha URLs novas em runtime, ou quando o usuario muda
    parametros e o plano original precisa de adendo.
    """
    path = Path(dest) / PLAN_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"PLAN.md nao existe em {dest}. Chame write_plan_md primeiro.")
    ts = datetime.now().isoformat(timespec="seconds")
    chunk = f"\n\n## {title} (anexado em {ts})\n\n{body.rstrip()}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(chunk)
    return path
