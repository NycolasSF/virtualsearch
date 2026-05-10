"""Toast notification Windows 10/11 via PowerShell (zero dependencia externa).

Usado por record_video.py / batch_record.py com --notify pra avisar quando
uma gravacao longa termina sem precisar ficar olhando o terminal.

Em SO nao-Windows o notify() vira no-op silencioso.
"""
from __future__ import annotations

import platform
import subprocess


_IS_WINDOWS = platform.system() == "Windows"


def notify(title: str, body: str, app_id: str = "VirtualSearch") -> bool:
    """Dispara toast nativo do Windows. Fire-and-forget.

    Retorna True se o subprocess foi lancado (nao garante que o toast apareceu —
    erros do PowerShell sao silenciados). False se SO nao-Windows ou subprocess
    falhou de cara.

    Erros sao silenciados de proposito: notificacao falhar nao deve quebrar a
    gravacao em andamento.
    """
    if not _IS_WINDOWS:
        return False

    safe_title = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    safe_body = (
        body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
    safe_app = app_id.replace("'", "''")

    ps = f"""
$ErrorActionPreference = 'SilentlyContinue'
$xml = @"
<toast>
  <visual>
    <binding template="ToastText02">
      <text id="1">{safe_title}</text>
      <text id="2">{safe_body}</text>
    </binding>
  </visual>
</toast>
"@
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.loadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{safe_app}').Show($toast)
"""
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "VirtualSearch"
    b = sys.argv[2] if len(sys.argv) > 2 else "Teste de notificacao"
    ok = notify(t, b)
    print(f"notify({t!r}, {b!r}) -> {ok}")
