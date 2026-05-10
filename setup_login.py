"""VirtualSearch — Helper de login persistente.

Abre Chromium HEADED apontando pro .profile-base/, navega pra URL informada,
espera voce logar manualmente e fecha. Cookies/storage persistem em
.profile-base/, e a partir dai qualquer outro script da skill em --mode profile
herdara o login via clone-on-start.

OBRIGATORIO: --url <pagina de login ou home apos login>.

Modos de espera (escolha um, ou nenhum = espera por input do usuario no console):

  --wait-selector <CSS>   : aguarda esse seletor aparecer na pagina (ex:
                            ".user-avatar", "[data-testid=logged-menu]"). Quando
                            aparecer, considera login ok e fecha. Default: nada.

  --wait-url-contains <s> : aguarda a URL conter <s> (ex: "/dashboard",
                            "/area/conteudo"). Util quando o site redireciona
                            apos login.

  --wait-seconds N        : aguarda N segundos antes de fechar (uso em sites
                            que so precisam de cookies setados sem ter elemento
                            previsivel).

Sem nenhum desses, fica aguardando voce dar Enter no terminal pra fechar.

Uso:
  # Manual: voce fecha quando terminar
  python setup_login.py --url https://site.com/login

  # Automatico via seletor (mais limpo)
  python setup_login.py --url https://site.com/login --wait-selector ".user-avatar"

  # Automatico via redirect (Hotmart/Orbyka, codigoviral, etc.)
  python setup_login.py --url https://cursos.codigoviral.com.br/area --wait-url-contains "/area/"

  # Tempo fixo (testes ou sites simples)
  python setup_login.py --url https://site.com --wait-seconds 60
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from browser_common import PROFILE_BASE, browser_session


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Login persistente no .profile-base/.")
    p.add_argument("--url", required=True, help="URL pra abrir (login ou home).")
    p.add_argument("--wait-selector", help="CSS aguardar aparecer pra fechar.")
    p.add_argument("--wait-url-contains", help="Substring na URL pra fechar.")
    p.add_argument("--wait-seconds", type=int,
                   help="Tempo fixo em segundos antes de fechar.")
    p.add_argument("--timeout", type=int, default=600,
                   help="Timeout maximo do helper (default 600s = 10min).")
    p.add_argument("--viewport", type=str, default=None,
                   help="Viewport WxH (ex: 1920x1080).")
    return p.parse_args()


def parse_viewport(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    try:
        w, h = s.lower().split("x")
        return (int(w), int(h))
    except Exception:
        return None


def main() -> int:
    args = parse_args()
    print(f"[setup_login] abrindo Chromium HEADED apontando pra {PROFILE_BASE}")
    print(f"[setup_login] URL alvo: {args.url}")

    if args.wait_selector:
        wait_kind = f"selector aparecer: {args.wait_selector}"
    elif args.wait_url_contains:
        wait_kind = f"URL conter: {args.wait_url_contains!r}"
    elif args.wait_seconds:
        wait_kind = f"{args.wait_seconds}s fixos"
    else:
        wait_kind = "voce dar Enter no terminal"
    print(f"[setup_login] vou esperar: {wait_kind} (timeout {args.timeout}s)")

    viewport = parse_viewport(args.viewport)

    try:
        with browser_session(
            mode="profile",
            headed=True,
            keep_profile=True,  # escreve direto no .profile-base
            url=args.url,
            viewport_size=viewport,
        ) as (page, context):
            t_start = time.time()

            if args.wait_selector:
                try:
                    page.wait_for_selector(
                        args.wait_selector,
                        state="visible",
                        timeout=args.timeout * 1000,
                    )
                    print(f"[setup_login] OK: selector apareceu apos {time.time()-t_start:.0f}s")
                except Exception as e:
                    print(f"[setup_login] timeout/erro aguardando selector: {e}", file=sys.stderr)
                    return 1

            elif args.wait_url_contains:
                deadline = time.time() + args.timeout
                while time.time() < deadline:
                    if args.wait_url_contains in page.url:
                        print(f"[setup_login] OK: URL bate apos {time.time()-t_start:.0f}s ({page.url})")
                        break
                    time.sleep(1)
                else:
                    print(f"[setup_login] timeout ({args.timeout}s) sem URL conter {args.wait_url_contains!r}",
                          file=sys.stderr)
                    return 1

            elif args.wait_seconds:
                time.sleep(min(args.wait_seconds, args.timeout))
                print(f"[setup_login] OK: {args.wait_seconds}s decorridos")

            else:
                print("[setup_login] Faca login na janela. Quando terminar, volte aqui e pressione Enter.")
                try:
                    input("> Enter para fechar e salvar profile: ")
                except (EOFError, KeyboardInterrupt):
                    print("\n[setup_login] interrompido — salvando assim mesmo")

            # Da uma respirada pra escritas finais de cookie/IndexedDB persistirem.
            time.sleep(1.5)

        print(f"[setup_login] profile salvo em: {PROFILE_BASE}")
        cookie_count = sum(1 for _ in PROFILE_BASE.rglob("Cookies")) if PROFILE_BASE.exists() else 0
        print(f"[setup_login] arquivos de Cookies encontrados: {cookie_count}")
        print("[setup_login] use os outros scripts em --mode profile (sem --keep-profile) — login herdado via clone")
        return 0

    except Exception as e:
        print(f"[setup_login] ERRO: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
