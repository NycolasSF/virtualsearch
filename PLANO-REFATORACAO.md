# Plano — Refatoração VirtualSearch para modos independentes

## Contexto

A VirtualSearch v0 depende do Edge CDP em `127.0.0.1:9224`, o mesmo que o `hotmart-recorder` usa. Problema: quando o recorder está gravando aula, o scraping trava esperando o Edge ficar livre. Também impede rodar **N instâncias paralelas** (só tem 1 Edge).

Objetivo: tornar a skill **independente do recorder** e **paralelizável** (múltiplas invocações simultâneas sem conflito), mantendo a opção de reaproveitar sessão logada quando for útil.

Decisão sobre agent: **não fazer agora**. Paralelismo puro (2 saves + 2 gravações simultâneos) é resolvido por scripts + múltiplos processos. Agent só ganha valor em batch com decisão dinâmica ou workflow composto — deixa pra quando o caso real aparecer.

---

## Três modos de execução

Todos os scripts aceitam flag `--mode <fresh|profile|cdp>`. Default: `profile`.

### Modo `fresh` (paralelo ilimitado, sem login)

- `chromium.launch(headless=True)` — browser novo a cada run.
- Sem cookies, sem storage. Não funciona em site gated.
- **Paralelismo: N sem conflito.** Cada processo Python = 1 Chromium isolado.
- Uso típico: scraping público (blogs, landing pages abertas, docs).

### Modo `profile` (default — paraleliza mantendo login)

- **Estratégia clone-on-start**: perfil base em `F:/claude-projetos/skills/virtualsearch/.profile-base/`. A cada run, o script copia pra `%TEMP%/vs-<pid>-<ts>/`, usa, e deleta no fim.
- Launch: `chromium.launch_persistent_context(temp_profile_path, headless=True)`.
- Primeira vez em cada site: logar manualmente rodando uma vez com `--headed --keep-profile` (grava no `.profile-base`). Depois os clones herdam o login.
- **Paralelismo: N sem conflito** (cada instância tem seu clone em temp diferente).
- Uso típico: Hotmart, painel SaaS, área de membros.
- Trade-off: ~1–2s de overhead por run (cópia do profile) + ~50–200MB de disco temporário.

### Modo `cdp` (opcional — reaproveita Edge do recorder)

- Conecta em `127.0.0.1:9224` como hoje (comportamento atual vira opt-in).
- **Serial** — 1 Edge só. Se recorder tá usando, espera.
- Uso típico: quando o usuário já navegou manualmente até o alvo e quer capturar dali sem re-logar.

---

## Mudanças por arquivo

### `browser_common.py`

**Novas funções:**
- `launch_browser(mode: str, headed: bool, keep_profile: bool)` — dispatcher.
- `_launch_fresh(pw, headed)` → `(browser, context)` com contexto transiente.
- `_launch_profile(pw, headed, keep_profile)` → clona `.profile-base` pra temp, retorna `(context, temp_path)`. `keep_profile=True` usa o base direto (escrita permanente — pra sessão de login inicial).
- `_connect_cdp_mode(pw)` → reaproveita `connect_cdp()` atual.
- `cleanup_temp_profile(path)` — remove temp profile no final (try/finally).

**Constantes:**
- `PROFILE_BASE = Path("F:/claude-projetos/skills/virtualsearch/.profile-base")`.

**API unificada pros scripts:**
```python
with browser_session(mode="profile") as (page, context):
    # page já aberta ou nova aba, context pronto pra .request.get()
    ...
```
Context manager cuida de cleanup (fecha browser em `fresh`/`profile`, só desanexa em `cdp`).

### `screenshot_page.py`, `scrape_images.py`, `scrape_text.py`, `scrape_viewsource.py`

Todos ganham:
- `--mode <fresh|profile|cdp>` (default: `profile`)
- `--headed` (flag — útil pra login inicial ou debug)
- `--keep-profile` (flag — só faz sentido com `--mode profile --headed`: escreve no `.profile-base` em vez de clone)

Corpo do `main()` muda de:
```python
pw, browser, context = connect_cdp()
try:
    page = get_active_page(context, url=args.url)
    ...
finally:
    pw.stop()
```
Para:
```python
with browser_session(args.mode, args.headed, args.keep_profile, args.url) as (page, context):
    ...
```

### `SKILL.md`

Atualiza:
- Seção "Dependências": remove CDP como obrigatório, marca como opcional (`--mode cdp`).
- Seção nova "Modos de execução" explicando os três e quando usar.
- Exemplos: adicionar um de login inicial (`--headed --keep-profile`) e um de batch paralelo (4 terminais rodando scripts ao mesmo tempo).
- Troubleshooting: entrada "profile lock" explicando que `--mode profile` é seguro pra paralelizar (por causa do clone), mas rodar 2 `--keep-profile` ao mesmo tempo trava.

---

## Impacto e compatibilidade

- **Quebra retrocompatível?** Ligeiramente. O default muda de CDP pra `profile`. Quem tinha script dependendo do Edge logado precisa adicionar `--mode cdp` explícito. Mas como a v0 tem poucas horas de vida e ainda não foi usada em produção, aceito.
- **hotmart-recorder**: zero impacto. Ele continua usando `connect_cdp()` da sua própria pasta, não importa nada da VirtualSearch.
- **Disco**: `.profile-base` pode chegar a ~100–500MB (cache/cookies). Temp clones giram e somem. Adicionar `.profile-base/` no `.gitignore` da skill.

---

## Checklist de execução

1. Adicionar `.gitignore` na pasta da skill ignorando `.profile-base/`, `__pycache__/`.
2. Reescrever `browser_common.py` com `browser_session` context manager e os 3 modos.
3. Refatorar os 4 scripts pra usar `browser_session` + argparse novos.
4. Atualizar `SKILL.md` (modos, exemplos, troubleshooting).
5. Smoke-test:
   - `python screenshot_page.py --url https://example.com --mode fresh` → PNG gerado.
   - `python scrape_text.py --url https://example.com --mode profile --format md` → MD gerado sem login.
   - `python screenshot_page.py --mode cdp` (com Edge aberto em :9224) → reaproveita aba ativa.
   - Paralelo: 2 shells rodando `scrape_images.py ... --mode fresh` simultaneamente → os dois terminam sem conflito.

---

## Fora de escopo (fica pra depois)

- **Agent `virtualsearch-orchestrator`**: só quando aparecer caso de batch com fallback ou workflow composto.
- **Suporte a Firefox/WebKit**: Playwright suporta, mas Chromium cobre 99% dos casos. Adicionar se surgir site que só funcione num engine diferente.
- **Pool de browsers pré-aquecidos**: otimização. Só se o overhead de 1–2s do clone virar gargalo.
- **Autenticação programática** (passar credencial pra script logar automaticamente): intencionalmente fora — mais risco de quebrar e expor credencial do que valor.
