# VirtualSearch — Bugs & Fixes (registro vivo)

Log **append-only** de bugs reais que aconteceram nesta skill (ou no roteamento dela) e as correcoes aplicadas.

Complementa, NAO substitui:
- `STATUS.md` -> checks automaticos (deps/smoke/paralelismo) + **gaps** (melhorias planejadas, G01-G06).
- **Este arquivo** -> **incidentes** que ja ocorreram: sintoma -> causa-raiz -> fix -> prevencao.

Diferenca pratica: um *gap* e algo que sabemos que falta. Um *bug* e algo que quebrou na pratica e levou a resultado errado.

---

## Quando registrar (obrigatorio)

Registre uma entrada sempre que:
- um script da skill se comportar diferente do esperado;
- houver **bug de roteamento** (o Claude usou a ferramenta/skill errada para a tarefa);
- uma mudanca causar **regressao**;
- qualquer surpresa levar a resultado errado de forma silenciosa.

## Como registrar

1. Copie o template abaixo, preencha e adicione a entrada **no topo da lista** (mais recente primeiro).
2. ID = `BF` + numero sequencial (BF01, BF02...).
3. Data no formato canonico CG (`DD/Mmm/AAAA` — usar a skill `timestamp-cg`, nao chutar).
4. Ao concluir um fix, logue tambem no checkdia (opcional): `ckd virtualsearch "<BFNN titulo>" -Done`.

```
### BFNN — <titulo curto>  ·  <DD/Mmm/AAAA>  ·  status: aberto | corrigido
- **Sintoma:** o que se observou (o comportamento errado).
- **Causa-raiz:** por que aconteceu.
- **Fix:** o que mudou (arquivo + descricao).
- **Prevencao:** regra/teste que evita recidiva. Pendencias, se houver.
```

---

## Incidentes

### BF01 — Link Hotmart caia em membros.orbyka.com  ·  04/Jun/2026  ·  status: corrigido
- **Sintoma:** ao invocar a skill com descricao + link da Hotmart, o conteudo capturado vinha de `membros.orbyka.com` (curso Rise), **ignorando o link passado**. O mesmo comportamento se repetia com links diferentes.
- **Causa-raiz:** o Claude, enviesado pela memoria do projeto (`reference_legendas_hls_hotmart.md`, que associa "legenda Hotmart" ao script especializado), rodou `_tmp/orbyka-recon/extrai_legendas.py`. Esse e um script **one-off da Rise** que NAO aceita `--url` — o alvo (`BASE = https://membros.orbyka.com/.../products/4939621/...`) esta **chumbado no codigo** (linha 16) e a navegacao usa `BASE + hash` das aulas da Rise. O link do operador foi **descartado silenciosamente**. (Confirmado tambem que o domino orbyka e so a fachada white-label da Hotmart Club: dos 312 requests de uma aula, so 2 vao pra orbyka.com; o resto e `*.hotmart.com`.)
- **Fix:** adicionada a secao **"⛔ ESCOPO DE EXECUCAO"** no topo do `SKILL.md`:
  1. so executa scripts de dentro de `skills/virtualsearch/` (script externo importar a skill nao o torna parte dela);
  2. scripts da skill sao ferramentas **genericas e imutaveis** — invocar com parametros, nunca editar para uma tarefa;
  3. dados de tarefa (lista de aulas, hashes) vao em **arquivo de input**, nunca hardcoded;
  4. faltando ferramenta nativa, **parar e avisar** em vez de gambiarrar.
  Roteamento correto para legenda/audio HLS: `hls_grab.py --url "<link>"` (respeita o link).
- **Prevencao:** guarda no `SKILL.md` (camada soft — vale quando a skill esta carregada). Pendencias opcionais ainda **nao** aplicadas:
  - ajustar `reference_legendas_hls_hotmart.md` na memoria para marcar `extrai_legendas.py` como one-off da Rise que ignora `--url`;
  - hook `PreToolUse` (camada hard, global) barrando execucao de `.py` sob `_tmp/`;
  - criar um **batch HLS nativo** (`hls_grab` so faz 1 URL; curso inteiro de legendas hoje nao tem ferramenta nativa de lote).
