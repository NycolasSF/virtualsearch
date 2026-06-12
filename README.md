# VirtualSearch

Toolkit **standalone** de captura programavel de conteudo web via [Playwright](https://playwright.dev/python/). Faz desde screenshot ate gravar curso inteiro em batch com transcricao automatica.

Roda Chromium proprio (fresh) ou perfil persistente com clone-on-start (profile) para paralelizar multiplas instancias sem conflito de lock. Cada execucao escreve dois arquivos no destino: `PLAN.md` (mapeamento + processo) escrito 1x no inicio, e `register.md` (checklist vivo + log timestamped) reescrito a cada passo. Nao existe execucao silenciosa.

> Skill projetada para uso com Claude Code, mas todos os scripts rodam standalone via CLI.

---

## O que faz

- **Screenshots** full-page ou por seletor CSS.
- **Download em lote de imagens** (`<img>` + `background-image`).
- **Extracao de texto estruturado** em Markdown (readability + fallback raw).
- **Bypass de anti-copy** via `view-source:`.
- **Captura HLS** (`hls_grab.py`): baixa a **legenda ASR pronta** (.txt + .vtt) e/ou a **trilha de audio** (.mp3, ffmpeg decripta AES-128) direto do player m3u8 — DOWNLOAD em velocidade de rede, ordens de magnitude mais rapido que gravar em tempo real.
- **Gravacao de aulas / videos** em qualquer player com tag `<video>` (Hotmart, Orbyka, Vimeo, Teachable, Udemy, Codigo Viral, Eduzz, Kiwify, players custom):
  - Audio principal via `MediaRecorder` + `captureStream()` (`.webm` opus 128k).
  - Dual-watchdog anti-truncate (JS + Python) para players com HLS/DASH + DRM tokens dinamicos.
  - Segmentacao por epoch + concat ffmpeg (sem re-encode).
  - Opcionalmente video do viewport via `record_video_dir` nativo do Playwright.
- **Extratores de SPA por interceptacao de backend** (filosofia: nao raspar a tela — achar o endpoint que alimenta a SPA e capturar a resposta crua):
  - `recon_network.py` — passo zero: loga toda resposta XHR/fetch/JSON de uma SPA para descobrir o endpoint.
  - `scrape_mindmeister.py` — mapa MindMeister COMPLETO (inclui nos colapsados/fora da viewport).
  - `scrape_manychat.py` — fluxo compartilhado do ManyChat (grafo de blocos com texto real das mensagens).
  - `scrape_typeform.py` — modelo completo de formulario/quiz Typeform (todas as perguntas + logica + tema).
  - `scrape_lookerstudio.py` — relatorio Looker Studio: estrutura + dados de cada grafico em CSV/Markdown.
- **Login persistente** (`setup_login.py` popula `.profile-base/` com cookies, demais scripts herdam via clone-on-start).
- **Batch** de varias URLs com skip-list por SHA1 e CLAUDE.md de progresso.
- **Transcricao automatica** via [audio-agent](https://github.com/NycolasSF/audio-agent) local (Whisper word-level, opcional).
- **Transcricao em lote** (`batch_transcribe.py`): transcreve midia ja gravada no disco, com uploads concorrentes ao audio-agent (`--parallel N`), skip automatico de quem ja tem `.txt` e modo recursivo.
- **Notificacoes toast** Windows 10/11.

---

## Instalacao

Requer Python 3.10+ e Git.

```powershell
git clone https://github.com/NycolasSF/virtualsearch.git
cd virtualsearch
pip install -r requirements.txt
python -m playwright install chromium
```

As deps do `requirements.txt`: `playwright` (browser), `readability-lxml` + `markdownify` (extracao de texto), `requests` (downloads HTTP: imagens, segmentos de legenda HLS) e `httpx` (so para o modo paralelo de `batch_transcribe.py`; sem ele o lote roda sequencial).

### Opcionais

- **ffmpeg** no PATH — usado por `hls_grab.py --want audio`/`--want both` (baixa + decripta AES-128 a trilha de audio; **sem ffmpeg a captura de audio via HLS nao funciona**, a de legenda sim) e por `record_video.py` (concat de re-arms; sem ele, fallback de append binario).
  ```powershell
  winget install Gyan.FFmpeg    # ou choco install ffmpeg
  # ou baixar binario em https://www.gyan.dev/ffmpeg/builds/
  ```
- **[audio-agent](https://github.com/NycolasSF/audio-agent)** rodando em `localhost:8020` — necessario para `--transcribe` (transcricao automatica via Whisper em `record_video.py`/`batch_record.py`) e para `batch_transcribe.py`. Sem ele a gravacao continua funcionando, so a transcricao e pulada. Instalar:
  ```powershell
  git clone https://github.com/NycolasSF/audio-agent.git
  cd audio-agent
  # seguir o README do proprio repo para subir o servico em localhost:8020
  ```

---

## Tres modos (`--mode`)

| Modo | Browser | Login | Paralelismo | Uso tipico |
|---|---|---|---|---|
| `fresh` | Chromium novo a cada run | Sem cookies | N ilimitado | Sites publicos, batch grande |
| `profile` (default) | Chromium com clone-on-start do `.profile-base/` | Persiste entre runs | N ilimitado | Sites gated (curso, SaaS, area de membros) |
| `cdp` | Edge externo em `127.0.0.1:9224` | Cookies do Edge | Serial (1 so) | Reusar browser ja aberto |

### Como funciona o `profile`

Existe um perfil base em `.profile-base/` (gitignored). A cada run, o script copia para `%TEMP%/virtualsearch-clones/clone-<pid>-<ts>/`, inicia o Chromium apontando para o clone, usa, e deleta no fim. Custo: ~1-2s de copia. Beneficio: **N instancias paralelas sem conflito de lock**.

### Login persistente

```powershell
# Voce loga manualmente, o script fecha sozinho quando o seletor aparece
python setup_login.py --url https://site.com/login --wait-selector ".user-avatar"

# Ou: redirect de URL apos login (mais robusto)
python setup_login.py --url https://cursos.codigoviral.com.br/login --wait-url-contains "/area/"

# Ou: tempo fixo (sites simples)
python setup_login.py --url https://site.com --wait-seconds 60

# Ou: voce avisa via Enter no terminal quando terminar
python setup_login.py --url https://site.com/login
```

A partir dai, qualquer outro script em `--mode profile` (sem `--keep-profile`) ja roda autenticado via clone.

---

## Scripts disponiveis

| Script | Funcao | Tipico |
|---|---|---|
| `screenshot_page.py` | PNG full-page ou por seletor CSS | Salvar hero, arquivar landing |
| `scrape_images.py` | Baixa `<img>` (srcset maior) + `background-image` em lote | Coletar assets visuais |
| `scrape_text.py` | HTML to Markdown via readability ou seletor | Extrair copy de blog, LP |
| `scrape_viewsource.py` | Bypass de anti-copy via `view-source:` | Copiar copy bloqueada |
| `hls_grab.py` | **HLS: baixa legenda (ASR) e/ou audio (.mp3) de player m3u8** | **Aula em player HLS — TENTAR ANTES de gravar** |
| `record_video.py` | Grava audio do `<video>` (e opcional viewport-video) de uma URL | Aula em curso, palestra VOD (fallback do HLS) |
| `batch_record.py` | Grava varias URLs em sequencia com skip-list + CLAUDE.md | Curso inteiro / playlist |
| `batch_transcribe.py` | Transcreve em lote midia ja no disco (uploads paralelos ao audio-agent) | Pasta de `.webm`/`.mp4` sem `.txt` |
| `recon_network.py` | Recon de rede de SPA: loga toda resposta XHR/JSON com corpos em disco | Passo zero antes de extrator novo |
| `scrape_mindmeister.py` | Mapa MindMeister completo via intercept de `content.json` | Mapa mental (nos colapsados inclusos) |
| `scrape_manychat.py` | Fluxo compartilhado ManyChat via `getSharedFlow` | Funil/chatbot WhatsApp |
| `scrape_typeform.py` | Modelo completo do form via `window.rendererData` | Formulario/quiz inteiro |
| `scrape_lookerstudio.py` | Estrutura + dados de relatorio Looker Studio (CSV + MD) | Dashboard de metricas |
| `setup_login.py` | Login persistente headed (popula `.profile-base/`) | 1x por site gated |
| `check_status.py` | Roda smoke tests e atualiza `STATUS.md` | Validar instalacao |

> ⚡ **Regra de ordem (aulas com player HLS — Hotmart/Orbyka etc.):** tente `hls_grab.py` PRIMEIRO (download da legenda pronta e/ou da trilha de audio — velocidade de rede) e so use `record_video.py`/`batch_record.py` (MediaRecorder, tempo real, lento) como fallback quando o HLS falhar. Em lote de 200+ aulas isso e a diferenca entre minutos e dias.

Modulos compartilhados (nao chamar direto):
- `browser_common.py` — `browser_session` context manager (3 modos + record_video opcional).
- `register.py` — `ExecutionRegister` (checklist vivo) + `validate_dest()` + `compute_default_dest()`.
- `plan.py` — `write_plan_md()` (PLAN.md inicial: mapeamento + processo).
- `video_record.py` — `BrowserVideoRecorder` (motor de captura de audio do `<video>`).
- `legenda_lib.py` — baixa/parseia segmentos webvtt de HLS (usado por `hls_grab.py`).
- `transcribe_helper.py` — integracao com audio-agent (Whisper); inclui `transcribe_many_async()` para lote paralelo.
- `win_notify.py` — toast Windows 10/11.

---

## `--dest` e opcional

Sem passar `--dest`, todos os scripts gravam num diretorio default (mundo `acervo` do cosmos, `F:\claude-projetos\acervo\library\`). Para isolar por captura, passe `--dest <pasta>` explicito:

```
--dest F:/projeto/concorrente-A-imagens/
--dest F:/aulas/curso-X/
```

A pasta sera criada se nao existir; falha se o path ja existe como arquivo.

**Padrao recomendado**: uma pasta por execucao (ou por "alvo logico"). Se voce rodar dois scripts apontando para o mesmo `--dest`, o segundo sobrescreve o `register.md` do primeiro.

```
F:/projeto/
├── concorrente-A-screenshots/   ← 1 execucao
│   ├── PLAN.md
│   ├── register.md
│   └── 2026-04-21-...-hero.png
├── concorrente-A-imagens/        ← outra execucao
│   └── 001-...jpg, 002-...
└── concorrente-B-copy/           ← outra execucao
    └── 2026-04-21-...-copy.md
```

---

## Exemplos

```powershell
# === Sites publicos (mais rapido, paralelo ilimitado) ===
python screenshot_page.py --dest F:/projeto/concorrente-A --url https://example.com --mode fresh
python scrape_text.py --dest F:/projeto/blog-X --url https://blog.com/post --mode fresh --format md

# === Sites com login (perfil persistente) ===
# 1. Login inicial (uma vez por site)
python setup_login.py --url https://site-gated.com/login --wait-selector ".user-avatar"

# 2. Daqui em diante, sem flags de login (clone-on-start)
python scrape_images.py --dest F:/capturas/site-gated --url https://site-gated.com/area --mode profile

# === Paralelo (4 terminais simultaneos) ===
# Cada script com seu --dest proprio (senao os register.md sobrescrevem um ao outro)
python scrape_images.py --dest F:/batch/site-A --url https://site-a.com --mode fresh
python scrape_text.py   --dest F:/batch/site-B --url https://site-b.com --mode fresh
python scrape_images.py --dest F:/batch/site-C --url https://hotmart.com/painel --mode profile
python scrape_text.py   --dest F:/batch/site-D --url https://outro-gated.com --mode profile

# === Reusar browser aberto ===
python screenshot_page.py --dest F:/capturas --mode cdp                          # aba ativa
python screenshot_page.py --dest F:/capturas --url https://... --mode cdp

# === Opcoes extras ===
python screenshot_page.py --dest F:/ --url https://... --selector "section.hero"
python scrape_images.py   --dest F:/ --url https://... --min-size 5120
python scrape_text.py     --dest F:/ --url https://... --format txt
python scrape_text.py     --dest F:/ --url https://... --raw
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

# === HLS: legenda/audio por download (tentar ANTES de gravar) ===
python hls_grab.py --url "https://.../aula/123" --dest F:/aulas/a123 --want legenda
python hls_grab.py --url "https://.../aula/123" --dest F:/aulas/a123 --want audio
python hls_grab.py --url "https://.../aula/123" --dest F:/aulas/a123 --want both --mode profile

# === Transcricao em lote de midia ja gravada ===
python batch_transcribe.py --path F:/aulas/curso-X                  # sequencial
python batch_transcribe.py --path F:/aulas/curso-X --parallel 3     # 3 uploads concorrentes
python batch_transcribe.py --path F:/aulas/curso-X --recursive      # varre subpastas
python batch_transcribe.py --path F:/aulas/curso-X --force          # re-transcreve tudo

# === Extratores de SPA (interceptacao de backend) ===
# Passo zero: descobrir de onde a SPA carrega os dados
python recon_network.py --url "https://app.exemplo.com/..." --headed --wait 25 --interact

# MindMeister (mapa compartilhado por link, token ?t= na URL)
python scrape_mindmeister.py --url "https://www.mindmeister.com/app/map/3621262441?t=gTWFXsgMN3"
python scrape_mindmeister.py --id 3621262441 --mode profile          # mapa privado seu

# ManyChat (fluxo compartilhado)
python scrape_manychat.py --url "https://app.manychat.com/flowPlayerPage?share_hash=..."

# Typeform (formulario/quiz completo)
python scrape_typeform.py --url "https://exemplo.typeform.com/to/OPpyh8wI"

# Looker Studio (estrutura; --data navega as paginas e extrai os numeros)
python scrape_lookerstudio.py --url "https://lookerstudio.google.com/reporting/<ID>"
python scrape_lookerstudio.py --url "..." --data --pages 5
```

---

## Captura HLS — `hls_grab.py`

Players HLS (Hotmart, Orbyka e similares) servem um `master.m3u8` assinado. Em vez de gravar em tempo real, o `hls_grab.py` captura a URL assinada do master (navegando com Playwright e dando play) e dai **baixa**:

- **`--want legenda`** — a faixa textstream (legenda ASR pronta da plataforma) -> `.txt` + `.vtt`. Sem audio, sem Whisper: a transcricao ja vem pronta.
- **`--want audio`** — a trilha de audio via ffmpeg (baixa + decripta AES-128) -> `.mp3` pronto para o Whisper.
- **`--want both`** — os dois.

**CDN learnings (aprendido na pratica):**
- `vod-akm.play.hotmart.com` (Akamai, token `hdntl` por-path) -> passar o MASTER ao ffmpeg.
- `contentplayer.hotmart.com` (CloudFront, Policy) -> usar a MENOR variante (sub-uri ja assinada no corpo do master), evita baixar video em alta resolucao.
- Downloads exigem headers `Referer`/`Origin` = `https://cf-embed.play.hotmart.com/`.

Quando o HLS falhar (player sem m3u8 acessivel, DRM custom), caia no `record_video.py` (gravacao em tempo real).

---

## Extratores de SPA — interceptacao de backend

Filosofia da familia: **nao raspar a tela**. Canvas e SPAs renderizam so o que esta na viewport (nos colapsados nem existem no DOM). A solucao robusta e abrir a pagina no Playwright, deixar a PROPRIA SPA chamar o backend com os cookies/headers/CSRF certos, e **interceptar a resposta crua**. Lossless e resiliente a mudanca de frontend.

| Alvo | Endpoint interceptado | Saida |
|---|---|---|
| `recon_network.py` | TODAS as respostas XHR/fetch/JSON/GraphQL | `network.log.md` + `bodies/*.json` + `summary.json` |
| MindMeister | `GET /maps/content.json?idea_id=...` | JSON cru + outline Markdown com 100% dos nos |
| ManyChat | `GET /manychat/getSharedFlow?share_hash=...` | JSON cru + grafo do fluxo (blocos, botoes, condicoes, A/B) |
| Typeform | `window.rendererData.form` embutido no HTML (brace-matching) | JSON do modelo + todas as perguntas/logica/tema |
| Looker Studio | `GET /getReport` (estrutura, prefixo `)]}'`) + `POST /batchedDataV2` (dados, lazy por pagina) | JSON cru + CSV + tabelas Markdown por grafico |

**Fluxo para alvo novo:** rode `recon_network.py` primeiro (ele loga toda resposta de rede promissora com corpo em disco), identifique o endpoint que carrega os dados, e dai escreva/peca um extrator dedicado no mesmo padrao.

Notas por extrator:
- **MindMeister**: o endpoint tem CSRF + sessao com estado (curl direto da 403); o share token `?t=` e resgatado pela propria SPA. Mapa publico -> `--mode fresh` com `--token`; mapa privado -> `--mode profile` apos `setup_login.py`.
- **ManyChat**: o fluxo e um grafo direcionado (na real, uma floresta de N entradas) — cada bloco tem `_oid` e aponta o proximo via `target._content_oid`. Tipos: `whatsapp`, `smart_delay`, `action_group`, `multi_condition`, `split` (A/B).
- **Typeform**: o modelo completo vem no HTML inicial; a chave `form:` nao tem aspas (objeto JS), o parser faz brace-matching ciente de strings antes do `json.loads`.
- **Looker Studio**: default extrai so a ESTRUTURA (1 request). `--data` navega pagina a pagina capturando o `batchedDataV2` de cada uma (`--pages N` limita). Rotulos legiveis das colunas vem do `getReport` (mapeados pelo nome interno `qt_xxx`).

---

## Transcricao em lote — `batch_transcribe.py`

Complementa o `batch_record.py` (que grava em sequencia): use quando voce **ja tem** os arquivos de midia no disco e quer os `.txt`.

- Extensoes default: `.webm .wav .mp4 .m4a .opus .mp3 .ogg .flac` (`--ext` filtra).
- **Idempotente**: pula quem ja tem `.txt` valido (>=100 bytes) ao lado; `--force` re-transcreve.
- `--parallel N` dispara N uploads concorrentes ao audio-agent (via `transcribe_many_async()` do `transcribe_helper.py`, requer `httpx`). Quem paraleliza de fato e o servidor: por default 1 Worker-GPU + 1 Worker-CPU de overflow; para paralelismo agressivo, suba `CPU_WORKERS=2..4` no `.env` do audio-agent.
- `--recursive` varre subpastas.

---

## Gravacao de video — arquitetura

### Captura de audio do `<video>` (sempre)

`record_video.py` injeta `MediaRecorder` no frame que contem o `<video>`, escuta `HTMLMediaElement.captureStream()` e baixa chunks opus de 2s diretamente para o disco via bridge CDP `expose_function`. Saida: `.webm` opus 128 kbps estereo (~0.96 MB/min).

**Localizacao do `<video>` (`--iframe-selector`):**
- `auto` (default): tenta frames filhos primeiro, depois cai no main frame.
- `main`: video direto na pagina principal (sem iframe).
- `<CSS selector>`: ex `'iframe[src*="player.hotmart.com"]'` para Hotmart, `'iframe[src*="vimeo"]'` para Vimeo.

**Duracao:**
- Default: le `video.duration` e roda ate `v.ended` ou `currentTime >= duration - 1.5`.
- `--duration N`: forca exatamente N segundos (uso em smoke test).
- `--max N`: teto de seguranca em segundos (default 4h).
- `--video-load-timeout N`: timeout esperando `readyState>=2 && duration>0` (default 30s).

### Dual-watchdog anti-truncate

Player com HLS/DASH + DRM tokens dinamicos (Hotmart/Orbyka, Codigo Viral, etc.) ocasionalmente reconstroi a `MediaStream` em segment boundary. A track antiga para de emitir dados, **`ondataavailable` nunca mais dispara, sem erro**. Sintoma: `.webm` finaliza com ~9 MB para aula de 90min.

Defesa em duas camadas:
- **Camada 1 — JS**: `setInterval` no navegador detecta stall (>30s sem chunk) e re-arma `MediaRecorder` com nova `MediaStream` do `captureStream()`.
- **Camada 2 — Python**: o loop principal chama `should_rearm()` a cada poll. Se stall >45s, dispara `force_rearm()` via `frame.evaluate()`. Pega o caso da aba ser pausada por tab throttling (Chromium minimizado).
- **Segmentacao**: cada re-arm incrementa `epoch` e abre nova `.partNN.webm`. No `stop()`, concat via `ffmpeg -f concat -c copy` (sem re-encode). Sem ffmpeg, fallback de append binario.
- **Validacao de saude**: taxa esperada >0.5 MB/min. Saida `<0.5 MB/min` indica truncate, **regrave**.

### Captura de video do viewport (opcional, com `--with-video`)

`--with-video` ativa o `record_video_dir` nativo do Playwright. Grava **frames + audio do viewport inteiro** (nao so do player) como WebM. Util quando voce quer ver tambem slides/UI ao redor do player, ou capturar players sem `<video>` semanticamente acessivel (ex: canvas/WebGL).

- Saida: `<filename>.viewport.webm` (mesma pasta do audio).
- `--viewport 1920x1080` define o tamanho do viewport (e do video gravado).
- **Limitacao 1 — DRM**: players com Widevine forte (Netflix/HBO/Disney+) renderizam preto na captura. A maioria dos cursos brasileiros (Hotmart, Orbyka, Codigo Viral, Eduzz, Kiwify) NAO usa Widevine forte e a captura funciona.
- **Limitacao 2 — incompativel com `--mode cdp`** (CDP nao suporta record_video).
- O audio gravado pelo MediaRecorder (do `<video>` direto) **continua sendo melhor** que o do viewport (que captura tudo via OS). Use os dois em paralelo: `.webm` para transcricao, `.viewport.webm` para arquivar visual.

### Pos-processamento

- `--transcribe`: depois de gravar, envia o `.webm` para o `audio-agent` em `localhost:8020` e salva `.txt` ao lado. Skip silencioso se o agent estiver offline.
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
- `--continue-on-error` — em caso de falha, segue para a proxima (default: para).
- `--no-skip-list` — ignora skip-list e regrava tudo.

---

## PLAN.md + register.md

Toda execucao da skill produz, dentro do `--dest`, dois arquivos obrigatorios:

1. **`PLAN.md`** — escrito UMA VEZ, no inicio. Documenta objetivo, mapeamento (escopo), artefatos esperados, parametros da execucao, processo de atualizacao de progresso, como acompanhar em tempo real, como saber se travou.
2. **`register.md`** — reescrito a CADA passo (flush sincrono). Mantem checklist `[ ] -> [>] -> [x]` (ou `[!]` falha, `[~]` pulado), log timestamped (`HH:MM:SS`), e secao `## Resultado` ao terminar.

Quando voce abre `--dest` em qualquer momento da execucao, o `PLAN.md` te diz o que era esperado e o `register.md` te diz onde a execucao esta agora.

---

## Paralelismo — regras

- **`--mode fresh`**: N instancias simultaneas, zero conflito.
- **`--mode profile` (sem `--keep-profile`)**: N instancias simultaneas (cada uma com clone temp).
- **`--mode profile --keep-profile`**: **exclusivo**. Use so para o login inicial.
- **`--mode cdp`**: serial (1 Edge so).
- **Regra do --dest**: cada instancia paralela precisa de `--dest` diferente, senao os register.md colidem.

Cada processo Python consome ~150-300MB RAM (Chromium headless).

---

## Troubleshooting

### `--dest existe mas nao eh pasta`
O path existe mas e um arquivo, nao um diretorio. Aponte para uma pasta (sera criada se nao existir).

### `ProcessSingleton / SingletonLock: profile is already in use`
Dois runs com `--keep-profile` ao mesmo tempo, ou crash anterior deixou lock. Em uso normal (sem `--keep-profile`), o clone evita isso.

### `scrape_images.py` retorna zero / poucas imagens
Default `--selector body` pode topar com lazy-load. Use `--selector main` (ou outro), ou `--mode cdp` apos rolar a pagina manualmente.

### `scrape_text.py` retorna conteudo truncado
Readability corta demais? Use `--selector main` explicito ou `--raw` para desligar readability.

### HTTP 403 em imagens
Cookie faltando. Em `--mode profile`, confirme que o site foi logado via `setup_login.py` antes. Em `fresh`, sites gated nao funcionam.

### `view-source:` retorna branco
Servidor bloqueia `X-Frame-Options` ou redireciona. Use `--format md` (navega na URL normal, converte HTML renderizado).

### `Executable doesn't exist at ...chromium...`
Falta baixar o browser: `python -m playwright install chromium`

### `record_video.py`: video nao carregou em N s
O `<video>` nao chegou a `readyState>=2` com `duration>0`. Causas comuns:
- Player exige clique de play primeiro -> use `--play-selector 'button...'`.
- Player esta em iframe nao detectado -> passe `--iframe-selector 'iframe[...]'` explicito.
- Pagina precisa de cookie de sessao -> use `--mode profile` apos login com `setup_login.py`.
- Player usa shadow DOM exotico (ex: alguns DRM custom) -> nao suportado hoje.

### `record_video.py`: terminou com tamanho minusculo (<5 MB para aula longa)
Truncate. Cheque taxa MB/min na secao `## Resultado` do `register.md`. Se `<0.5`, regrave. Se watchdogs nao atuaram (`re-arms=0`, `js-rearms=0`, `py-rearms=0`), pode ser que o player exija ajuste fino.

### `record_video.py`: ffmpeg nao encontrado no PATH
Re-arms multiplos (raros) cairao em fallback binario. Para prevenir, instale ffmpeg e adicione no PATH.

### `record_video.py --transcribe`: pulou transcricao
Mensagem `audio-agent offline em :8020`: o agent nao esta rodando. O `.webm` continua salvo, voce pode transcrever depois com `python transcribe_helper.py <path.webm>`.

### `record_video.py --with-video`: viewport.webm vem em preto
Player com Widevine DRM forte (Netflix-tier). Captura de viewport nao consegue ler frames protegidos. So o audio (`.webm` principal) funciona nesse caso.

### `record_video.py --with-video`: erro `incompativel com --mode cdp`
CDP nao suporta `record_video_dir` do Playwright. Use `--mode fresh` ou `--mode profile`.

### `batch_record.py`: cada URL aparece pulada como "ja em .skip-list.json"
Voce ja gravou tudo antes. Para forcar regravar uma URL especifica: edite `<dest>/.skip-list.json` e remova a entrada da chave. Para forcar tudo: `--no-skip-list`.

### `setup_login.py`: timeout sem aparecer o seletor
Selector errado ou login nao foi feito. Cheque o seletor com DevTools no Chromium aberto. Ou use `--wait-url-contains` (mais robusto) ou rode sem nenhum (manual: voce da Enter quando terminar).

---

## Quando usar

- "salvar imagem de `<site>`"
- "copiar copy/texto desse site"
- "baixar todas as imagens de `<url>`"
- "screenshot da pagina inteira de `<site>`"
- "arquivar essa landing page"
- "o site bloqueou meu copiar-colar, preciso do texto"
- "extrair copy de concorrente para analise"
- "preciso capturar varios sites em paralelo"
- "gravar essa aula/video", "preciso da transcricao desse video em pagina"
- "baixar a legenda pronta dessa aula" / "pegar o audio da aula sem gravar em tempo real" (`hls_grab.py`)
- "gravar uma aula avulsa de `<site qualquer>`" (qualquer player com `<video>`)
- "gravar o curso inteiro de `<plataforma>`" (Hotmart, Orbyka, Codigo Viral, Eduzz, Kiwify, Vimeo, Teachable, etc.) via batch
- "gravar com video tambem, nao so audio" (`--with-video`)
- "transcrever essa pasta de gravacoes" (`batch_transcribe.py`)
- "extrair o mapa mental completo do MindMeister" (`scrape_mindmeister.py`)
- "extrair o fluxo compartilhado do ManyChat" (`scrape_manychat.py`)
- "extrair todas as perguntas desse Typeform" (`scrape_typeform.py`)
- "extrair estrutura e dados desse Looker Studio" (`scrape_lookerstudio.py`)
- "descobrir de onde essa SPA carrega os dados" (`recon_network.py`)
- "logar nesse site uma vez e deixar persistido para as proximas capturas" (`setup_login.py`)

## Quando NAO usar

- **Gravar player com Widevine DRM forte** (Netflix/HBO/Disney+) -> nao funciona; o `<video>` retorna black frames para `captureStream()`.
- **Capturar reuniao/call ao vivo (Zoom, Meet)** -> use o audio-agent direto (loopback WASAPI).
- **Transcrever 1 arquivo avulso ja no disco** -> `python transcribe_helper.py <path>` direto (para pasta inteira, ai sim `batch_transcribe.py`).

---

## Arquitetura — notas

- **Standalone**: nao depende de nenhuma outra skill. Tem seu proprio motor de gravacao com namespace JS `__vsrec*`, bridge functions proprias, e helpers de transcricao + notify embutidos.
- **Ferramentas genericas e imutaveis**: os scripts sao parametrizados por CLI (`--url`, `--urls`, `--dest`, `--want`...). Dados de tarefa (lista de aulas, URLs especificas) vao em arquivo de input, nunca hardcoded no codigo. Detalhe em `SKILL.md` (secao "Escopo de execucao").
- **Idempotente**: rodar de novo gera arquivo timestampado novo dentro do `--dest`. `--skip-if-exists` (em `record_video.py`), `.skip-list.json` (em `batch_record.py`) e o skip por `.txt` existente (em `batch_transcribe.py`) garantem que re-rodar nao refaz o que ja foi feito.
- **Nao faz login automatico**: intencional. Login e via `setup_login.py`, rodado uma vez por site. Credenciais nunca passam por argumento de CLI.
- **Ffmpeg / audio-agent opcionais**: a skill detecta na hora e degrada graciosamente. Sem ffmpeg, fallback binario no concat. Sem audio-agent, `--transcribe` skipa silencioso (o `.webm` continua salvo).
- **Registro de incidentes**: bugs reais (sintoma -> causa-raiz -> fix -> prevencao) ficam em `BUGFIXES.md`, append-only. Distinto do `STATUS.md`, que cobre checks automaticos + gaps planejados.

---

## Estrutura do repositorio

```
virtualsearch/
├── README.md                  ← este arquivo
├── INSTALL.md                 ← guia rapido de instalacao
├── SKILL.md                   ← skill descriptor para Claude Code
├── STATUS.md                  ← auto-gerado por check_status.py
├── BUGFIXES.md                ← registro append-only de incidentes (sintoma -> fix)
├── PLANO-REFATORACAO.md       ← notas de refatoracao em curso
├── requirements.txt           ← deps Python
├── .gitignore
│
├── screenshot_page.py
├── scrape_images.py
├── scrape_text.py
├── scrape_viewsource.py
├── hls_grab.py                ← legenda/audio HLS por download (pre-record)
├── record_video.py
├── batch_record.py
├── batch_transcribe.py        ← transcricao em lote de midia ja no disco
├── recon_network.py           ← recon de rede de SPA (passo zero de extrator)
├── scrape_mindmeister.py      ← extrator MindMeister (content.json)
├── scrape_manychat.py         ← extrator ManyChat (getSharedFlow)
├── scrape_typeform.py         ← extrator Typeform (window.rendererData)
├── scrape_lookerstudio.py     ← extrator Looker Studio (getReport + batchedDataV2)
├── setup_login.py
├── check_status.py
│
├── browser_common.py          ← context manager + 3 modos
├── register.py                ← ExecutionRegister (register.md vivo)
├── plan.py                    ← write_plan_md (PLAN.md inicial)
├── video_record.py            ← BrowserVideoRecorder (audio do <video>)
├── legenda_lib.py             ← segmentos webvtt de HLS (usado por hls_grab)
├── transcribe_helper.py       ← integracao com audio-agent (+ lote async)
└── win_notify.py              ← toast Windows 10/11
```

Itens gitignored: `.profile-base/` (cookies de login), `.profile-clones/`, `__pycache__/`, `.status-state.json`, `.test-output/`, `output/`.

---

## Licenca

Uso pessoal / interno Marcio Medeiros Educacao. Sem licenca aberta no momento.
