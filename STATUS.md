# VirtualSearch — Status & Checklist

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

<!-- AUTO:START -->

_Gerado em 2026-05-04T17:58:13_

**Resumo:** 21/34 passando (62%) | falhas: 0 | pulados: 0 | pendentes: 13

### Dependencias

| ID | Status | Check | Ultima execucao | Nota |
|---|---|---|---|---|
| D01 | [x] `pass` | playwright importavel _(auto)_ | 2026-05-04T17:57:48 | playwright importavel |
| D02 | [x] `pass` | readability-lxml importavel _(auto)_ | 2026-05-04T17:57:48 | readability importavel |
| D03 | [x] `pass` | markdownify importavel _(auto)_ | 2026-05-04T17:57:48 | markdownify importavel |
| D04 | [x] `pass` | Chromium instalado (playwright) _(auto)_ | 2026-05-04T17:57:49 | chromium launch ok |
| D05 | [x] `pass` | ffmpeg no PATH (record_video opcional) _(auto)_ | 2026-05-04T17:57:49 | ffmpeg em C:\Users\nycol\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Mic |
| D06 | [x] `pass` | requests importavel (transcribe_helper) _(auto)_ | 2026-05-04T17:57:49 | requests importavel |

### Smoke tests

| ID | Status | Check | Ultima execucao | Nota |
|---|---|---|---|---|
| T01 | [x] `pass` | screenshot_page --mode fresh _(auto)_ | 2026-05-04T17:57:54 | 5.1s \|      register: F:\claude-projetos\skills\virtualsearch\.test-output\t01\ |
| T02 | [x] `pass` | scrape_text --mode fresh (readability) _(auto)_ | 2026-05-04T17:57:56 | 2.2s \|      register: F:\claude-projetos\skills\virtualsearch\.test-output\t02\ |
| T03 | [x] `pass` | scrape_images --mode fresh _(auto)_ | 2026-05-04T17:58:03 | 6.6s \|      register: F:\claude-projetos\skills\virtualsearch\.test-output\t03\ |
| T04 | [x] `pass` | scrape_viewsource --format html _(auto)_ | 2026-05-04T17:58:05 | 2.2s \|      register: F:\claude-projetos\skills\virtualsearch\.test-output\t04\ |
| T05 | [x] `pass` | scrape_viewsource --format md _(auto)_ | 2026-05-04T17:58:07 | 2.2s \|      register: F:\claude-projetos\skills\virtualsearch\.test-output\t05\ |
| T06 | [x] `pass` | --dest obrigatorio (falha sem --dest) _(auto)_ | 2026-05-04T17:58:07 | falhou corretamente (exit=2) |
| T07 | [x] `pass` | record_video.py --help responde _(auto)_ | 2026-05-04T17:58:08 | help responde com flags esperadas |
| T08 | [x] `pass` | record_video.py --dest obrigatorio _(auto)_ | 2026-05-04T17:58:08 | falhou corretamente (exit=2) |
| T09 | [x] `pass` | transcribe_helper imports OK _(auto)_ | 2026-05-04T17:58:08 | is_audio_agent_up + transcribe_to_txt importaveis |
| T15 | [x] `pass` | win_notify import OK _(auto)_ | 2026-05-04T17:58:08 | win_notify.notify importavel |
| T16 | [x] `pass` | setup_login.py --help responde _(auto)_ | 2026-05-04T17:58:09 | help responde com flags esperadas |
| T17 | [x] `pass` | batch_record.py --help responde _(auto)_ | 2026-05-04T17:58:09 | help responde com flags esperadas |
| T18 | [x] `pass` | audio-agent online em :8020 (opcional) _(auto)_ | 2026-05-04T17:58:11 | audio-agent online em localhost:8020 |

### Modos

| ID | Status | Check | Ultima execucao | Nota |
|---|---|---|---|---|
| T10 | [x] `pass` | .profile-base existe e esta populado _(auto)_ | 2026-05-04T17:58:11 | .profile-base populado |
| T11 | [ ] `pending` | --mode profile em site gated (manual) _(manual)_ | - |  |
| T12 | [ ] `pending` | --mode cdp conecta em Edge :9224 (manual, requer Edge aberto) _(manual)_ | - |  |
| T13 | [ ] `pending` | record_video.py em site real com video (manual, exige login) _(manual)_ | - |  |
| T14 | [ ] `pending` | setup_login.py popula .profile-base em site real (manual) _(manual)_ | - |  |
| T19 | [ ] `pending` | batch_record.py em site real com 2+ URLs (manual) _(manual)_ | - |  |
| T20 | [ ] `pending` | record_video.py --with-video viewport.webm em player sem DRM (manual) _(manual)_ | - |  |

### Paralelismo

| ID | Status | Check | Ultima execucao | Nota |
|---|---|---|---|---|
| P01 | [x] `pass` | 2x --mode fresh paralelo sem conflito _(auto)_ | 2026-05-04T17:58:13 | 2x paralelo fresh em 2.2s |
| P02 | [ ] `pending` | 2x --mode profile paralelo com clones distintos (manual) _(manual)_ | - |  |

### Gaps / Melhorias

| ID | Status | Check | Ultima execucao | Nota |
|---|---|---|---|---|
| G01 | [ ] `pending` | User-Agent customizado (--user-agent flag) _(manual)_ | - |  |
| G02 | [ ] `pending` | Scroll automatico em scrape_images (lazy-load) _(manual)_ | - |  |
| G03 | [ ] `pending` | Retry em falhas de rede _(manual)_ | - |  |
| G04 | [ ] `pending` | Timeout de goto configuravel via flag _(manual)_ | - |  |
| G05 | [ ] `pending` | Bypass anti-copy validado em site real com user-select:none _(manual)_ | - |  |
| G06 | [ ] `pending` | record_video: validar dual-watchdog em player que reconstrua MediaStream em campo _(manual)_ | - |  |

<!-- AUTO:END -->

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
