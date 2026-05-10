---
name: virtualsearch
description: Toolkit standalone de captura programavel de conteudo web via Playwright. Engloba screenshots full-page ou por seletor CSS, download em lote de imagens (<img> + background-image), extracao de texto estruturado em Markdown (readability), bypass de anti-copy via view-source, e gravacao de aulas/videos em qualquer player com tag <video> — captura audio (MediaRecorder + dual-watchdog anti-truncate, segmentacao por epoch e concat ffmpeg) e opcionalmente video do viewport (Playwright record_video). Tem helpers integrados para login persistente (setup_login.py popula .profile-base com cookies), batch de varias URLs com skip-list e CLAUDE.md de progresso (batch_record.py), transcricao automatica via audio-agent local (audio + Whisper), e notificacoes toast no Windows. Totalmente independente: roda Chromium proprio (fresh) ou perfil persistente com clone-on-start (profile) para paralelizar multiplas instancias sem conflito de lock. CADA execucao SEMPRE escreve dois arquivos no destino: PLAN.md (mapeamento + processo) escrito 1x no inicio, e register.md (checklist vivo + log timestamped) reescrito a cada passo. --dest e opcional — sem ele, default = F:\claude-projetos\library\ (raiz). Use esta skill SEMPRE que o pedido envolver salvar imagem de site, copiar texto de site, screenshot de pagina, scraping programavel, capturar conteudo de landing page, extrair copy de concorrente, baixar imagens em lote, bypass de user-select:none, gravar audio (e/ou video do viewport) de aula em qualquer plataforma de cursos (Hotmart, Orbyka, Kajabi, Vimeo, Teachable, Udemy, plataformas brasileiras como Codigo Viral / Eduzz / Kiwify, ou players custom), gravar varias URLs em batch, automatizar login persistente em sites gated, ou capturar conteudo em paralelo de multiplos sites.
path: F:/claude-projetos/skills/virtualsearch
---

# VirtualSearch

Toolkit **standalone** de captura programavel de conteudo web com Playwright. Faz desde screenshot ate gravar curso inteiro em batch com transcricao automatica.

## Plano de mapeamento + Processo de atualizacao (regra invariante)

**Toda execucao da skill produz, dentro do `--dest`, dois arquivos obrigatorios:**

1. **`PLAN.md`** — escrito UMA VEZ, no inicio. Documenta:
   - Objetivo da captura.
   - Mapeamento (escopo: o que esta dentro / fora).
   - Artefatos esperados (lista do que vai ser gerado nessa pasta).
   - Parametros da execucao (modo, iframe-selector, viewport, etc.).
   - Processo de atualizacao de progresso (regras de leitura).
   - Como acompanhar em tempo real e como saber se travou.
2. **`register.md`** — reescrito a CADA passo (flush sincrono). Mantem:
   - Checklist `[ ] -> [>] -> [x]` (ou `[!]` falha, `[~]` pulado).
   - Log timestamped (`HH:MM:SS`) com cada evento.
   - Secao `## Resultado` ao terminar.

Quando voce abre `--dest` em qualquer momento da execucao, o `PLAN.md` te diz o que era esperado e o `register.md` te diz onde a execucao esta agora. **Nao existe execucao silenciosa.**

## `--dest` e opcional — default = `F:\claude-projetos\library`

Sem passar `--dest`, todo script grava em:
```
F:\claude-projetos\library\
```
Os arquivos gerados ali:
- `<ts>-<slug>.webm` / `.png` / `.md` / `.txt` — artefatos com timestamp no nome (nao colidem entre runs)
- `PLAN.md` — escrito no inicio de cada execucao (sera **sobrescrito** se outra execucao rodar com mesmo `--dest`)
- `register.md` — checklist vivo (idem PLAN.md, **sobrescrito** por nova execucao)

**Trade-off do default:** runs sucessivas sem `--dest` partilham `library\` — os artefatos coexistem (timestamp unico), mas o PLAN/register ficam sempre sendo do **ultimo run**. Isso e intencional pra ter um lugar previsivel pra inspecionar a captura mais recente.

**Se quiser histor isolado por captura**, passe `--dest <pasta>` explicito:
```
--dest F:/claude-projetos/library/curso-codigoviral-aula-1636820/
--dest F:/projeto-X/concorrente-A-imagens/
```

A pasta sera criada se nao existir; falha se o path ja existe como arquivo.

Quando rodar via Claude Code em linguagem natural, o agente **mostra o caminho final antes de iniciar**. Se nada for dito, usa o default `F:\claude-projetos\library\`.

---

## Tres modos (`--mode`)

| Modo | Browser | Login | Paralelismo | Uso tipico |
|---|---|---|---|---|
| `fresh` | Chromium novo a cada run | Sem cookies | N ilimitado | Sites publicos, batch grande |
| `profile` (default) | Chromium com clone-on-start do `.profile-base/` | Persiste entre runs | N ilimitado | Sites gated (curso, SaaS, area de membros) |
| `cdp` | Edge externo em `127.0.0.1:9224` | Cookies do Edge | Serial (1 so) | Reusar browser ja aberto |

### Como funciona o `profile`

Existe um perfil base em `skills/virtualsearch/.profile-base/` (gitignored). A cada run, o script copia pra `%TEMP%/virtualsearch-clones/clone-<pid>-<ts>/`, inicia o Chromium apontando pro clone, usa, e deleta no fim. Custo: ~1-2s de copia. Beneficio: **N instancias paralelas sem conflito de lock**.

Pra popular o `.profile-base` com login, use o helper dedicado:
```bash
# Voce loga manualmente, ele fecha sozinho quando o seletor aparece
python setup_login.py --url https://site.com/login --wait-selector ".user-avatar"

# Ou: redirect de URL apos login
python setup_login.py --url https://cursos.codigoviral.com.br/login --wait-url-contains "/area/"

# Ou: tempo fixo (sites simples)
python setup_login.py --url https://site.com --wait-seconds 60

# Ou: voce avisa via Enter no terminal quando terminar
python setup_login.py --url https://site.com/login
```
A partir dai, qualquer outro script em `--mode profile` (sem `--keep-profile`) ja roda autenticado via clone.

---

## Dependencias

```bash
pip install playwright readability-lxml markdownify requests
python -m playwright install chromium
```

**Opcionais** (skill segue funcionando sem):
- `ffmpeg` no PATH — concat de re-arms em `record_video.py`. Sem ele, fallback de append binario (menos robusto, mas funciona).
- `audio-agent` rodando em `localhost:8020` — necessario apenas para `--transcribe`. Subir com `cd F:/claude-projetos/audio-agent && python main.py`.

---

## Scripts disponiveis

| Script | Funcao | Tipico |
|---|---|---|
| `screenshot_page.py` | PNG full-page ou por seletor CSS | Salvar hero, arquivar landing |
| `scrape_images.py` | Baixa `<img>` (srcset maior) + `background-image` em lote | Coletar assets visuais |
| `scrape_text.py` | HTML -> Markdown via readability ou seletor | Extrair copy de blog, LP |
| `scrape_viewsource.py` | Bypass de anti-copy via `view-source:` | Copiar copy bloqueada |
| `record_video.py` | Grava audio do `<video>` (e opcional viewport-video) de uma URL | Aula em curso, palestra VOD |
| `batch_record.py` | Grava varias URLs em sequencia com skip-list + CLAUDE.md | Curso inteiro / playlist |
| `setup_login.py` | Login persistente headed (popula `.profile-base/`) | 1x por site gated |

Modulos compartilhados (nao chamar direto):
- `browser_common.py` — `browser_session` context manager (3 modos + record_video opcional)
- `register.py` — `ExecutionRegister` (checklist vivo) + `validate_dest()` + `compute_default_dest()`
- `plan.py` — `write_plan_md()` (PLAN.md inicial: mapeamento + processo)
- `video_record.py` — `BrowserVideoRecorder` (motor de captura de audio do `<video>`)
- `transcribe_helper.py` — integracao com audio-agent (Whisper)
- `win_notify.py` — toast Windows 10/11

---

## PLAN.md — o que e gerado no inicio

Logo apos a invocacao, antes de abrir o browser, a skill escreve um `PLAN.md` no `--dest`:

```markdown
# VirtualSearch — Plano de mapeamento + processo de atualizacao

**Script:** `record_video.py`
**URL:** https://cursos.codigoviral.com.br/area/conteudo/aula/1636820
**Destino:** F:/claude-projetos/library/cursos-codigoviral-com-br__...
**Modo:** profile
**Plano gerado em:** 2026-05-04T18:15:30

## Objetivo
Gravar audio do <video> de `<URL>` em `.webm` (opus 128k) e transcrever via audio-agent.

## Mapeamento (escopo)
- Localizar elemento <video> no frame correto (auto/main/css selector).
- Capturar audio via MediaRecorder + dual-watchdog anti-truncate.
- NAO captura frames do viewport (so audio). Use `--with-video` se precisar.
- Apos gravar, enviar ao audio-agent (`localhost:8020`) e salvar `.txt`.
- Toast Windows ao terminar.

## Artefatos esperados
- `<ts>-<slug-titulo>.webm` — audio principal (opus 128k stereo).
- `<ts>-<slug-titulo>.txt` — transcricao via audio-agent.
- `register.md` — checklist vivo da execucao (passos + log + resultado).
- `PLAN.md` — este arquivo (mapeamento + processo).
- `<ts>-<slug-titulo>.partNN.webm` — partes intermediarias se houver re-arm.

## Parametros da execucao
- iframe_selector: auto
- play_rate: 1.0
- silent: True
- transcribe: True
- notify: True
- ...

## Processo de atualizacao de progresso
- `register.md` na mesma pasta e reescrito apos CADA passo (flush sincrono).
- Cada passo tem 4 estados visiveis no checklist: `[ ]` -> `[>]` -> `[x]`/`[!]`/`[~]`.
- A secao `## Log` recebe uma linha timestamped por evento.
- Eventos pontuais (re-arms, marcas de progresso 25/50/75%, downloads concluidos)
  viram linhas extras de `note` no log.
- Ao terminar, `## Resultado` consolida tamanhos e estatisticas.

## Como acompanhar em tempo real
- Abra `<dest>/register.md` em qualquer editor.
- Marcas: [ ] pendente, [>] ativo, [x] ok, [!] falhou, [~] pulado.

## Como saber se travou
- `register.md` deixa de receber novas linhas em `## Log` por mais de ~30s.
- Para gravacao, o tamanho do `.webm` para de crescer.

## Apos terminar
- `register.md` fica com status concluido | falhou | parcial.
- Os arquivos gerados ficam nessa mesma pasta.
- `PLAN.md` permanece imutavel (referencia do que foi planejado).
```

`PLAN.md` e estavel — escrito 1x. `register.md` e o que muda em tempo real (proxima secao).

## register.md — o que fica salvo

Cada execucao gera um `register.md` dentro do `--dest`:

```markdown
# VirtualSearch — Registro de execucao

**Script:** `scrape_images.py`
**URL:** https://concorrente.com
**Destino:** F:/projeto-X/captura-concorrente
**Modo:** profile
**Iniciado em:** 2026-04-21T01:15:32
**Status:** em progresso

**selector:** main
**min_size_bytes:** 1024

## Passos

- [x] Conectar browser (mode=profile)
- [x] Navegar para URL — _url=https://concorrente.com/_
- [x] Coletar candidatas dentro de 'main' — _24 candidatas_
- [>] Baixar imagens (preenchido apos coleta)
- [ ] Cleanup

## Log

- 01:15:32 — inicio | script=scrape_images.py | url=... | mode=profile
- 01:15:33 — plano definido com 5 passos
- 01:15:34 — start  | Conectar browser (mode=profile)
- 01:15:36 — done   | Conectar browser (mode=profile)
...
```

Quando termina, ganha secao `## Resultado` com resumo. Dá pra abrir esse arquivo enquanto o script roda pra acompanhar progresso em tempo real.

---

## Exemplos

```bash
cd F:/claude-projetos/skills/virtualsearch

# === Sites publicos (mais rapido, paralelo ilimitado) ===
python screenshot_page.py --dest F:/projeto/concorrente-A --url https://example.com --mode fresh
python scrape_text.py --dest F:/projeto/blog-X --url https://blog.com/post --mode fresh --format md

# === Sites com login (perfil persistente) ===
# 1. Login inicial (uma vez por site)
python screenshot_page.py --dest F:/setup --url https://site-gated.com --mode profile --headed --keep-profile
# ... voce loga na janela que abriu ...

# 2. Daqui em diante, sem --headed --keep-profile (usa clone)
python scrape_images.py --dest F:/capturas/site-gated --url https://site-gated.com/area --mode profile

# === Paralelo (4 terminais simultaneos) ===
# Cada script com seu --dest proprio (senao os register.md sobrescrevem um ao outro)
# Terminal 1:
python scrape_images.py --dest F:/batch/site-A --url https://site-a.com --mode fresh
# Terminal 2:
python scrape_text.py --dest F:/batch/site-B --url https://site-b.com --mode fresh
# Terminal 3:
python scrape_images.py --dest F:/batch/site-C --url https://hotmart.com/painel --mode profile
# Terminal 4:
python scrape_text.py --dest F:/batch/site-D --url https://outro-gated.com --mode profile

# === Reusar browser aberto ===
python screenshot_page.py --dest F:/capturas --mode cdp                          # aba ativa
python screenshot_page.py --dest F:/capturas --url https://... --mode cdp

# === Opcoes extras ===
python screenshot_page.py --dest F:/ --url https://... --selector "section.hero"
python scrape_images.py --dest F:/ --url https://... --min-size 5120
python scrape_text.py --dest F:/ --url https://... --format txt
python scrape_text.py --dest F:/ --url https://... --raw
python scrape_viewsource.py --dest F:/ --url https://... --format md

# === Gravacao de video / aula ===
# Login inicial no site gated (1x por site)
python setup_login.py --url https://cursos.codigoviral.com.br/login \
    --wait-url-contains "/area/"

# Gravar 1 aula (auto-detect iframe, audio so, transcribe + notify ao fim)
python record_video.py --dest F:/aulas/cv-1636820 \
    --url https://cursos.codigoviral.com.br/area/conteudo/aula/1636820 \
    --mode profile --transcribe --notify

# Gravar com video do viewport tambem (frames + audio em 1080p)
python record_video.py --dest F:/aulas/X --url https://... \
    --with-video --viewport 1920x1080

# Iframe explicito (Hotmart, Vimeo, JW, Brightcove...)
python record_video.py --dest F:/cap/Z --url https://... \
    --iframe-selector 'iframe[src*="player.com"]'

# Video direto no main frame (sem iframe)
python record_video.py --dest F:/cap/Z --url https://... --iframe-selector main

# Teste rapido de 30s (ignora video.duration)
python record_video.py --dest F:/cap/test --url https://... --duration 30

# Acelerar captura 1.5x (opus 128k mantem qualidade)
python record_video.py --dest F:/cap/X --url https://... --play-rate 1.5

# Click num botao de play antes de gravar (players que exigem)
python record_video.py --dest F:/cap/X --url https://... \
    --play-selector 'button.vjs-big-play-button'

# === Batch: curso inteiro / playlist ===
# Crie um aulas.txt com 1 URL por linha (# comenta), depois:
python batch_record.py --dest F:/aulas/curso-cv --urls aulas.txt \
    --mode profile --transcribe --notify

# Re-rodar pula o que ja foi gravado (skip-list por hash de URL):
python batch_record.py --dest F:/aulas/curso-cv --urls aulas.txt --mode profile

# Forcar regravar tudo:
python batch_record.py --dest F:/aulas/curso-cv --urls aulas.txt --no-skip-list

# Comecar da aula 5, processar so 3:
python batch_record.py --dest F:/aulas/curso-cv --urls aulas.txt \
    --start-from 5 --limit 3
```

---

## Gravacao de video — arquitetura completa

### Captura de audio do `<video>` (sempre)

**Como funciona:** `record_video.py` injeta `MediaRecorder` no frame que contem o `<video>`, escuta `HTMLMediaElement.captureStream()` e baixa chunks opus de 2s diretamente pro disco via bridge CDP `expose_function`. Saida: `.webm` opus 128 kbps estereo (~0.96 MB/min).

**Localizacao do `<video>` (`--iframe-selector`):**
- `auto` (default): tenta frames filhos primeiro, depois cai no main frame.
- `main`: video direto na pagina principal (sem iframe).
- `<CSS selector>`: ex `'iframe[src*="player.hotmart.com"]'` para Hotmart, `'iframe[src*="vimeo"]'` para Vimeo, `'iframe[src*="player.codigoviral.com.br"]'` para Codigo Viral.

**Duracao:**
- Default: le `video.duration` e roda ate `v.ended` ou `currentTime >= duration - 1.5`.
- `--duration N`: forca exatamente N segundos (uso em smoke test).
- `--max N`: teto de seguranca em segundos (default 4h).
- `--video-load-timeout N`: timeout esperando `readyState>=2 && duration>0` (default 30s).

### Dual-watchdog anti-truncate

Player com HLS/DASH + DRM tokens dinamicos (Hotmart/Orbyka, Codigo Viral, etc.) ocasionalmente reconstroi a `MediaStream` em segment boundary. A track antiga para de emitir dados, **`ondataavailable` nunca mais dispara, sem erro**. Sintoma: `.webm` finaliza com ~9 MB pra aula de 90min.

Defesa em duas camadas:
- **Camada 1 — JS**: `setInterval` no navegador detecta stall (>30s sem chunk) e re-arma `MediaRecorder` com nova `MediaStream` do `captureStream()`.
- **Camada 2 — Python**: o loop principal chama `should_rearm()` a cada poll. Se stall >45s, dispara `force_rearm()` via `frame.evaluate()`. Pega o caso da aba ser pausada por tab throttling (Chromium minimizado).
- **Segmentacao**: cada re-arm incrementa `epoch` e abre nova `.partNN.webm`. No `stop()`, concat via `ffmpeg -f concat -c copy` (sem re-encode). Sem `ffmpeg`, fallback de append binario.
- **Validacao de saude**: taxa esperada >0.5 MB/min. Saida `<0.5 MB/min` indica truncate, **regrave**.

### Captura de video do viewport (opcional, com `--with-video`)

`--with-video` ativa o `record_video_dir` nativo do Playwright. Grava **frames + audio do viewport inteiro** (nao so do player) como WebM. Util quando voce quer ver tambem slides/UI ao redor do player, ou capturar players sem `<video>` semanticamente acessivel (ex: canvas/WebGL).

- Saida: `<filename>.viewport.webm` (mesma pasta do audio).
- `--viewport 1920x1080` define o tamanho do viewport (e do video gravado).
- **Limitacao 1 — DRM**: players com Widevine forte (Netflix/HBO/Disney+) renderizam preto na captura. A maioria dos cursos brasileiros (Hotmart, Orbyka, Codigo Viral, Eduzz, Kiwify) NAO usa Widevine forte e a captura funciona.
- **Limitacao 2 — incompativel com `--mode cdp`** (CDP nao suporta record_video).
- O audio gravado pelo MediaRecorder (do `<video>` direto) **continua sendo melhor** que o do viewport (que captura tudo via OS). Use os dois em paralelo: `.webm` pra transcricao, `.viewport.webm` pra arquivar visual.

### Pos-processamento

- `--transcribe`: depois de gravar, envia o `.webm` pro `audio-agent` em `localhost:8020` e salva `.txt` ao lado. Skip silencioso se o agent estiver offline.
- `--notify`: toast Windows ao terminar (ou no-op em outros SOs).
- `--skip-if-exists`: se ja existir `.webm` com filename alvo, pula a gravacao (util quando voce passa `--filename` explicito ou quer re-rodar comando idempotente).

### Batch — gravar varios videos em sequencia

`batch_record.py` recebe um arquivo `.txt` com 1 URL por linha (`#` comenta) e processa cada uma usando os mesmos parametros globais. Mantem:

- `<dest>/.skip-list.json` — chave SHA1 da URL marca completos. Por padrao, re-rodar pula automaticamente.
- `<dest>/CLAUDE.md` — checklist `[x]/[~]/[!]/[ ]` de cada URL (com filename + tamanho) + log timestamp.
- `<dest>/_per-url/<NNN-hash>/register.md` — register individual de cada URL (mesmo formato do `record_video.py`).
- `<dest>/register.md` — register central da run de batch (uma linha por URL).

Flags uteis:
- `--start-from N` — comeca da Nth URL.
- `--limit N` — processa no maximo N URLs nesta run.
- `--continue-on-error` — em caso de falha, segue pra proxima (default: para).
- `--no-skip-list` — ignora skip-list e regrava tudo.

---

## Convencao de --dest

Como cada execucao gera um `register.md`, **uma pasta por execucao** (ou por "alvo logico") e o padrao mais limpo. Se voce rodar dois scripts apontando pro mesmo `--dest`, o segundo **sobrescreve** o `register.md` do primeiro.

Padrao recomendado:
```
F:/projeto/
├── concorrente-A-screenshots/    ← 1 execucao
│   ├── register.md
│   └── 2026-04-21-...-hero.png
├── concorrente-A-imagens/         ← outra execucao
│   ├── register.md
│   └── 001-...jpg, 002-..., ...
└── concorrente-B-copy/            ← outra execucao
    ├── register.md
    └── 2026-04-21-...-copy.md
```

---

## Paralelismo — regras

- **`--mode fresh`**: N instancias simultaneas, zero conflito.
- **`--mode profile` (sem `--keep-profile`)**: N instancias simultaneas (cada uma com clone temp).
- **`--mode profile --keep-profile`**: **exclusivo**. Use soh pro login inicial.
- **`--mode cdp`**: serial (1 Edge so).
- **Regra do --dest:** cada instancia paralela precisa de `--dest` diferente, senao os register.md colidem.

Cada processo Python consome ~150-300MB RAM (Chromium headless).

---

## Troubleshooting

### `--dest existe mas nao eh pasta`
O path existe mas e um arquivo, nao um diretorio. Aponte pra uma pasta (sera criada se nao existir).

### `ProcessSingleton / SingletonLock: profile is already in use`
Dois runs com `--keep-profile` ao mesmo tempo, ou crash anterior deixou lock. Em uso normal (sem `--keep-profile`), o clone evita isso.

### `scrape_images.py` retorna zero / poucas imagens
Default `--selector body` pode topar com lazy-load. Use `--selector main` (ou outro), ou `--mode cdp` apos rolar a pagina manualmente.

### `scrape_text.py` retorna conteudo truncado
Readability corta demais? Use `--selector main` explicito ou `--raw` pra desligar readability.

### HTTP 403 em imagens
Cookie faltando. Em `--mode profile`, confirme que o site foi logado via `--keep-profile` antes. Em `fresh`, sites gated nao funcionam.

### `view-source:` retorna branco
Servidor bloqueia `X-Frame-Options` ou redireciona. Use `--format md` (navega na URL normal, converte HTML renderizado).

### `Executable doesn't exist at ...chromium...`
Falta baixar o browser: `python -m playwright install chromium`

### `record_video.py`: video nao carregou em N s
O `<video>` nao chegou a `readyState>=2` com `duration>0`. Causas comuns:
- Player exige clique de play primeiro -> use `--play-selector 'button...'`.
- Player esta em iframe nao detectado -> passe `--iframe-selector 'iframe[...]'` explicito.
- Pagina precisa de cookie de sessao -> use `--mode profile` apos login com `--keep-profile`.
- Player usa shadow DOM exotico (ex: alguns DRM custom) -> nao suportado hoje.

### `record_video.py`: terminou com tamanho minusculo (<5 MB pra aula longa)
Truncate. Cheque taxa MB/min na secao `## Resultado` do `register.md`. Se `<0.5`, regrave. Se watchdogs nao atuaram (`re-arms=0`, `js-rearms=0`, `py-rearms=0`), pode ser que o player exija ajuste fino (ex: video.muted=true zerando track) — abra issue.

### `record_video.py`: ffmpeg nao encontrado no PATH
Re-arms multiplos (raros) cairao em fallback binario. Pra prevenir, instale ffmpeg e adicione no PATH (Windows: `choco install ffmpeg` ou baixe binario gyan.dev).

### `record_video.py --transcribe`: pulou transcricao
Mensagem `audio-agent offline em :8020`: o agent nao esta rodando. Suba com `cd F:/claude-projetos/audio-agent && python main.py`. O `.webm` continua salvo, voce pode transcrever depois com `python transcribe_helper.py <path.webm>`.

### `record_video.py --with-video`: viewport.webm vem em preto
Player com Widevine DRM forte (Netflix-tier). Captura de viewport nao consegue ler frames protegidos. So o audio (`.webm` principal) funciona nesse caso. Cursos brasileiros tipicos (Hotmart, Codigo Viral, Eduzz, Kiwify) nao usam Widevine forte.

### `record_video.py --with-video`: erro `incompativel com --mode cdp`
CDP nao suporta `record_video_dir` do Playwright. Use `--mode fresh` ou `--mode profile`.

### `batch_record.py`: cada URL aparece pulada como "ja em .skip-list.json"
Voce ja gravou tudo antes. Pra forcar regravar uma URL especifica: edite `<dest>/.skip-list.json` e remova a entrada da chave. Pra forcar tudo: `--no-skip-list`.

### `setup_login.py`: timeout sem aparecer o seletor
Selector errado ou login nao foi feito. Cheque o seletor com DevTools no Chromium aberto. Ou use `--wait-url-contains` (mais robusto) ou rode sem nenhum (manual: voce da Enter quando terminar).

---

## Quando usar esta skill

- "salvar imagem de `<site>`"
- "copiar copy/texto desse site"
- "baixar todas as imagens de `<url>`"
- "screenshot da pagina inteira de `<site>`"
- "arquivar essa landing page"
- "o site bloqueou meu copiar-colar, preciso do texto"
- "extrair copy de concorrente pra analise"
- "preciso capturar varios sites em paralelo"
- "gravar essa aula/video", "preciso da transcricao desse video em pagina"
- "gravar uma aula avulsa de `<site qualquer>`" (qualquer player com `<video>`)
- "gravar o curso inteiro de `<plataforma>`" (Hotmart, Orbyka, Codigo Viral, Eduzz, Kiwify, Vimeo, Teachable, etc.) via batch
- "gravar com video tambem, nao so audio" (`--with-video`)
- "logar nesse site uma vez e deixar persistido pras proximas capturas" (`setup_login.py`)

## Quando NAO usar

- **Gravar player com Widevine DRM forte** (Netflix/HBO/Disney+) -> nao funciona; o `<video>` retorna black frames pra `captureStream()`.
- **Capturar reuniao/call ao vivo (Zoom, Meet)** -> use o audio-agent direto (loopback WASAPI).
- **Gerar criativo novo** -> use `adsmith` ou `gerar-imagem`.
- **Transcricao de audio ja baixado** -> use `audio-agent` direto (sem precisar reabrir browser).

---

## Workflow via Claude Code (linguagem natural)

Quando invocada por conversa, Claude **DEVE**:

1. **Resolver o `--dest`**: se o usuario nao especificou, computar o default (`F:/claude-projetos/library/<slug>__<ts>/`) e **mostrar o caminho final antes de iniciar** (sem perguntar — se o usuario quiser outro lugar, ele intervem). Se especificou, usar o caminho dele.
2. Escolher o modo apropriado (`fresh` pra publico, `profile` pra gated, `cdp` soh se o usuario explicitar).
3. **Pra sites gated, sempre passar pelo `setup_login.py` primeiro** se `.profile-base` ainda nao tem login pro dominio alvo. Nunca colar credenciais no terminal — abrir headed e deixar o usuario logar.
4. **Pra `record_video.py` / `batch_record.py`**: confirmar **o que capturar**:
   - audio so (default — `.webm` opus)?
   - audio + viewport video (`--with-video`)?
   - transcrever no fim (`--transcribe`)?
   - notificar no fim (`--notify`)?
5. **Pra batch**: pedir o arquivo `.txt` de URLs ou pedir pra colar a lista pra montar (1 URL por linha, `#` comenta).
6. Disparar o script e apontar **`PLAN.md`** (escopo) **+ `register.md`** (progresso vivo) pra o usuario acompanhar.
7. No final, resumir o que foi salvo e em qual pasta (incluindo `.viewport.webm`, `.txt` de transcricao, e CLAUDE.md em batches).

## Notas

- **Standalone**: skill nao depende de nenhuma outra (nem do hotmart-recorder). Tem seu proprio motor de gravacao com namespace JS `__vsrec*`, bridge functions proprias, e helpers de transcricao + notify embutidos.
- **Idempotente**: rodar de novo gera arquivo timestampado novo dentro do `--dest`. `--skip-if-exists` (em `record_video.py`) e `.skip-list.json` (em `batch_record.py`) garantem que re-rodar nao regrava o que ja foi feito.
- **Nao faz login automatico**: intencional. Login e via `setup_login.py`, rodado uma vez por site. Credenciais nunca passam por argumento de CLI.
- **Ffmpeg / audio-agent opcionais**: a skill detecta na hora e degrada graciosamente. Sem ffmpeg, fallback binario no concat. Sem audio-agent, `--transcribe` skipa silencioso (o `.webm` continua salvo).
