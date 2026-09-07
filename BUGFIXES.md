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

### BF07 — `hls_curso.py` apontado para plataforma nao-Hotmart trava em silencio ate o timeout  ·  05/Set/2026  ·  status: corrigido
- **Sintoma:** `hls_curso.py --course-url <MasterClass>` abriu o browser, logou, navegou ate a pagina certa do curso — e ficou parado ate os 600s sem escrever `register.md`, sem erro e sem log. Aparentava problema de login/Cloudflare; nao era.
- **Causa-raiz:** `hls_curso.py` e **especifico do Hotmart Club** (docstring linha 2). Ele bloqueia num "GATE de prontidao" esperando a resposta do endpoint `/navigation` **da Hotmart** chegar pela rede, para so entao extrair o curriculo. Em qualquer outra plataforma esse sinal nunca chega e o script espera em silencio. O nome generico (`hls_curso`) nao denuncia o acoplamento.
- **Fix:** criado **`vtt_grab.py`** — ferramenta generica que le a tag `<track>` do DOM (main frame + iframes) e baixa o WebVTT pela sessao autenticada (`context.request`). Sem HLS, sem `/navigation`, sem Whisper. Serve qualquer site com legenda nativa; alvo sempre por `--url`/`--urls`.
- **Prevencao:** tabela de scripts (README/SKILL) agora marca `hls_curso.py` como **Hotmart Club apenas** e aponta `vtt_grab.py` como a via para as demais plataformas. Antes de usar `hls_curso.py` fora da Hotmart: nao usar.
- **Achado colateral (importante):** em site com **DRM/EME** (MasterClass usa MSE + `blob:`), o player **nao inicializa em headless** e as `<track>` nunca entram no DOM — `vtt_grab.py` retorna "nenhuma <track> encontrada". Com `--headed` funciona. Nao e bug: e requisito. Por isso `vtt_grab.py` documenta `--headed` para player com DRM.
- **Bug de uso na mesma sessao:** `setup_login.py --wait-url-contains "/homepage"` chamado pelo **Git Bash** teve o argumento convertido pelo MSYS para `C:/Program Files/Git/homepage`, tornando a condicao de saida impossivel — o operador logou e "nada aconteceu". Chamar os scripts pelo **PowerShell** quando algum argumento comeca com `/` (ver `memory/feedback_msys_path_conv.md`).

---

### BF06 — `--transcribe` / `batch_transcribe.py` falham 100% em silencio (endpoint de auth morto)  ·  14/Ago/2026  ·  status: aberto
- **Sintoma:** `batch_transcribe.py --path <164 m4a> --recursive` terminou com **exit code 0** e `0 ok / 164 erro em 0.1min`. Nenhum `.txt` gerado. O transcritor estava no ar (UI respondia em :8020), a GPU estava livre e os arquivos existiam — nada indicava a causa. Vale para todo `--transcribe` da skill, nao so o batch.
- **Causa-raiz:** `transcribe_helper.py:59` chama `POST /auth/dev-login`, endpoint que **nao existe mais** no transcritor (o `openapi.json` de hoje expoe `/auth/register` e `/auth/login`). Pior: `_get_token()` captura `RequestException` e retorna `None`, e `_headers()` devolve `{}` quando nao ha token — o upload segue sem `Authorization` e morre em auth. O 404 nunca aparece para o operador; o erro se disfarca de "erro por arquivo".
- **Fix:** ainda **nao aplicado** no `transcribe_helper.py`. Contornado nesta captura escrevendo o lote contra o cliente canonico do motor (`_infra/transcritor/clients/transcritor_client.py`, via `wait_server()` + `transcribe_media(token=...)`), que e a rota que `tool_transcritor.md` manda usar. Fix pendente = trocar `/auth/dev-login` por `/auth/login` com credencial, ou (melhor) fazer o helper delegar ao `transcritor_client` em vez de falar HTTP na mao.
- **Prevencao:** (1) `_get_token()` nao pode falhar em silencio — se o login der 404/401, propagar erro claro ("endpoint de auth mudou") em vez de devolver `None`; (2) `batch_transcribe.py` nao deveria sair com exit 0 quando 100% dos itens falham; (3) helper que duplica cliente oficial vira drift — a skill deveria importar `transcritor_client` e nao manter copia da API.

### BF05 — emoji no titulo faz o script reportar "falhou" DEPOIS de ter concluido  ·  13/Ago/2026  ·  status: corrigido
- **Sintoma:** no lote dos 87 cursos do INEMA, `scrape_text.py` reportou `FALHOU landing` em `claude-skills` com `UnicodeEncodeError: 'charmap' codec can't encode character '⚡'`. Mas o `.md` **estava salvo** e o proprio `register.md` tinha `status=concluido` seguido de `status=falhou` na linha de baixo.
- **Causa-raiz:** o titulo do curso comeca com ⚡, que entra no nome do arquivo. O `print(f"[OK] {out_path}")` fica **dentro do `try`**, e o stdout do Windows e cp1252 — o print estourou, o `except` capturou e chamou `reg.finish("falhou")` por cima de um `finish("concluido")` que ja tinha rodado. Falso negativo puro: o driver ve exit≠0, marca falha e a captura seria refeita a toa.
- **Fix:** `register.py` (importado por **todos** os scripts CLI da skill) agora faz `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")` no import — um ponto, todos os callers. Preferido a mexer nos ~11 scripts ou a exigir `PYTHONIOENCODING` de quem chama.
- **Prevencao:** ao ler resultado de lote, conferir o `register.md` antes de acreditar no exit code — `concluido` seguido de `falhou` e a assinatura desse bug. Qualquer script novo da skill que importe `register` ja nasce com a saida em UTF-8.

### BF04 — faltava ferramenta de crawl; e o `strip=` do markdownify vaza JS pro markdown  ·  13/Ago/2026  ·  status: corrigido
- **Sintoma (1, gap):** atualizar o library do INEMA exigia capturar as licoes de 87 cursos estaticos (`inematds.github.io/<slug>/`). `scrape_text.py` faz **1 URL por vez** e nao descobre links — em junho isso foi resolvido por subagentes ad-hoc montando `licoes_urls.txt` na mao, exatamente o anti-padrao que a secao "ESCOPO DE EXECUCAO" proibe.
- **Sintoma (2, bug):** o primeiro teste do crawler novo gerou paginas com **270 KB** onde havia ~147 KB de conteudo — o corpo dos `<script>` (tema, localStorage, analytics) vazou inteiro pro markdown.
- **Causa-raiz:** (1) gap real de ferramenta, previsto na pendencia do BF01. (2) `markdownify(html, strip=["script","style"])` remove a **tag**, nao o **texto** dentro dela; para descartar o conteudo o certo e `remove` (nao suportado em todas as versoes) ou tirar o bloco do HTML antes de converter.
- **Fix:** criado `crawl_site.py` — BFS restrito ao prefixo da URL, HTTP puro (`requests`, sem browser), markdown espelhando o path, `INDICE.md`, `PLAN.md`/`register.md` no padrao da skill, `--workers`/`--max-pages`/`--subdir`/`--skip-root`, e `--self-check` (asserts das regras de URL/path, sem rede). O `html_to_markdown` remove `script/style/noscript/template/svg` por regex **antes** do markdownify.
- **Prevencao:** rodar `python crawl_site.py --self-check` apos mexer nas regras de URL. Ao converter HTML->MD em qualquer script novo, **nunca confiar no `strip=`** — limpar o HTML antes. Nota de escopo: HTTP puro nao executa JS; para SPA que monta conteudo no cliente, continua sendo `scrape_text.py --mode fresh` pagina a pagina (o register conta as paginas "magras" para denunciar isso).

### BF03 — `hls_curso` era a rota errada para curso que existe no Hotmart Player  ·  11/Ago/2026  ·  status: corrigido
- **Sintoma:** extracao do acervo do Romulo/FiscoSim (139 aulas, 30,9h) rodando por `hls_curso.py --always-audio` ia levar **~21h**: medido **1,25 MB/min**, praticamente tempo real, porque o CDN do HLS entrega no ritmo de playback.
- **Causa-raiz:** roteamento. O mesmo material estava no **Hotmart Player** (`play.hotmart.com/library`) como `.mp4` bruto com **URL assinada de download direto** — ninguem tinha mapeado essa API, entao a unica rota conhecida era o HLS do Club. O mapa antigo registrava `401` no endpoint de pastas e isso fechou a porta cedo demais; o `401` era so falta do header `Authorization`.
- **Fix:** criado `player_grab.py` (varre a biblioteca, baixa `.mp4`, extrai `.mp3`). Medido **~11 MB/s (660 MB/min)** — **~400x mais rapido**. Acervo inteiro: 153 arquivos em **45 min**, 0 falhas. Documentado em `memory/reference_hotmart_player_api.md`.
- **Prevencao:** **antes de puxar audio por HLS, checar se o curso existe no Player.** O `code` do media no Player e o mesmo `firstMediaCode` do `navigation.json` do Club, entao da para conferir a correspondencia sem baixar nada. Dois headers sao obrigatorios na API: `Authorization: Bearer` **e** `hotmart-target-account-id` — sem o segundo a API responde `200` com `content` VAZIO (falha silenciosa que parece "biblioteca vazia").

### BF02 — `batch_transcribe.py` falha em 100% dos arquivos (auth)  ·  11/Ago/2026  ·  status: aberto
- **Sintoma:** `batch_transcribe.py --path <pasta> --recursive` marcou **erro em todos os 153 arquivos** com `auth dev-login falhou (DEV_AUTO_LOGIN=true no .env do agent?)`. O servidor estava no ar e saudavel (`is_transcritor_up()` = True), entao o script parecia "rodar" e so produzia falha.
- **Causa-raiz:** `transcribe_helper.py:59` so sabe autenticar por `POST /auth/dev-login`, que esta **desligado** neste servidor. O cliente canonico (`_infra/transcritor/clients/transcritor_client.py`) usa `POST /auth/login` com `TRANSCRITOR_EMAIL`/`TRANSCRITOR_PASSWORD` do `.env` — funciona de primeira.
- **Fix (contornado, nao corrigido na skill):** o lote do Romulo rodou por script proprio usando o cliente canonico (`CLIENTES/romulo-fiscosim/hotmart-player-acervo/_transcrever.py`). 153/153 transcritos.
- **Prevencao / pendencia:** trocar o auth do `transcribe_helper.py` para o cliente canonico (ou fazer fallback `dev-login` -> `/auth/login`). Enquanto isso, **nao usar `batch_transcribe.py` deste hub** — usar o cliente canonico direto. Nota extra: o `--ext` default inclui `.mp4`, entao rodar sobre uma pasta com `.mp4`+`.mp3` transcreve o mesmo conteudo duas vezes; passar `--ext .mp3`.

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

## 2026-09-03 — `slides_grab.py`: `--repeat-stop` não detecta fim em deck com fundo animado

**Sintoma.** Captura do protótipo Figma "Slides Camp Baldan" (CAMP #01) seguiu até o teto de
frames capturando 196 vezes o mesmo slide final ("BRINDAREMOS"). O deck tem 27 slides reais.

**Causa.** A detecção de fim compara **SHA1 do PNG** entre prints consecutivos. O slide final
tem fundo animado (partículas em movimento), então cada print difere em pixels mesmo sendo o
mesmo slide — o hash nunca repete e `--repeat-stop` nunca dispara.

**Contorno usado.** Passar `--count <n>` quando o total é conhecido (o Figma Slides mostra
"1 / N" no DOM em modo Audience; protótipo `/proto/` não mostra). Sem isso, revisar a captura
e separar os frames redundantes depois.

**Correção proposta (não implementada).** Trocar hash exato por diferença perceptual: comparar
os PNGs redimensionados a ~64px em escala de cinza e considerar "igual" abaixo de um limiar
(ex.: `--repeat-tolerance 2`, em % de pixels diferentes). Isso ignora ruído de animação de
fundo e ainda pega slide realmente repetido. Enquanto não for feito, o comportamento seguro
é sempre passar `--count` quando o número de slides for conhecido.

**Registro relacionado.** O auto-crop (`slides_montage.py`) também falha em slide de fundo
preto puro: a detecção de borda pela cor do canto come conteúdo e gera células de alturas
diferentes. Contorno: `--no-autocrop` em deck de fundo escuro uniforme.
