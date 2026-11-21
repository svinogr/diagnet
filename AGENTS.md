# Сетевая диагностика — портативный набор

Почасовая (или по расписанию) проверка качества сети с веб-дашбордом.
Windows 10 (1803+) / 11, только встроенные средства: cmd, PowerShell,
curl, tar. Python требуется только для дашборда и подготавливается
автоматически (embeddable package из python.org, кладётся в `python\`).

## Структура

- `ЗАПУСК.bat` — единая точка входа: создаёт ярлык с иконкой (первый
  запуск), стартует сервер дашборда через `service\start_viewer_silent.bat`,
  открывает браузер.
- `icon.ico` — иконка набора, ярлыка и favicon дашборда
  (генерируется `make_icon.py` в корне рабочей копии).
- `service\viewer.py` — HTTP-сервер дашборда на stdlib (127.0.0.1:8471):
  журналы, настройки, запуск проверок, управление задачами Планировщика.
  Все данные читает/пишет в `..\data\`.
- `data\` — журналы (создаётся автоматически): `netdiag_results.md`,
  `hop_results.md`. Код сюда не смотрит, только данные.
- `service\diag.bat` — основная проверка (ping/DNS UDP+TCP/HTTPS/tracert),
  читает параметры из `service\config.bat`, пишет в `data\netdiag_results.md`.
- `service\hopcheck.ps1` — проверка по узлам маршрута (стиль WinMTR):
  параллельный ping каждого хопа, пишет в `data\hop_results.md`.
- `service\netdiag_wrapper.bat` — обёртка над diag.bat: перенаправляет
  вывод, вызывает append_to_md.ps1, код возврата = результат проверки.
- `service\append_to_md.ps1` — дописывает запись в MD-журнал (UTF-8 BOM).
- `service\config.bat` — параметры (создаёт/перезаписывает дашборд).
- `service\start_viewer_silent.bat` — поиск Python (PATH → типичные пути
  → `python\` → авто-загрузка embeddable) и старт viewer.py.
- `service\install_task.bat`, `service\install_viewer_autostart.bat` —
  резервный способ создать задачи без дашборда.

## Задачи Планировщика

- `NetDiagHourly` — основная проверка, `/SC MINUTE /MO N`.
- `NetDiagHopCheck` — проверка по узлам, `/SC MINUTE /MO N`
  (по умолчанию каждые 30 мин, 1000 пакетов ≈ 17 мин).

## Соглашения (важно при правках)

- `.bat` — UTF-8 без BOM, вторая строка `chcp 65001 >nul`
  (русский текст не зависит от локали системы).
- `.ps1` — UTF-8 с BOM (иначе Windows PowerShell 5.1 ломает кириллицу).
- `config.bat` — только ASCII; значения проходят валидацию в viewer.py.
- Журналы `*.md` — UTF-8 с BOM, LF; запись через .NET
  (AppendAllText с UTF8Encoding($true)), блоки `## / **Результат:** / ```text`.
- Вывод cmd-утилит читается как OEM (CP866) или UTF-8 после `chcp 65001`
  (в hopcheck.ps1 — обе разновидности).
- Дашборд слушает только 127.0.0.1:8471, без внешних зависимостей.
- POST-запросы к дашборду требуют заголовок `X-Requested-With`
  (защита от CSRF); значения конфига валидируются, одинарная кавычка
  в именах хостов запрещена (защита от инъекций в PowerShell).

## Дашборд

Endpoints: `/`, `/api/entries`, `/api/hops`, `/api/config`,
`/api/task*`, `/api/hoptask*`, `/api/run`, `/api/hoprun`, `/api/hopstatus`.
Управление задачами идёт через `schtasks` (при необходимости —
UAC-повышение через `Start-Process -Verb RunAs -Wait`).
