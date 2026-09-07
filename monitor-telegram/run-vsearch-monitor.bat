@echo off
cd /d "F:\claude-projetos\_skills\virtualsearch\monitor-telegram"
"C:\Users\nycol\AppData\Local\Programs\Python\Python312\python.exe" vsearch_progress_monitor.py --dest "F:\claude-projetos\_acervo\library\cbschool\audios" --total 29 --label "CBSchool (29 aulas)" --task "VSearch Monitor - CBSchool" >> monitor-stdout.log 2>&1
exit /b %errorlevel%
