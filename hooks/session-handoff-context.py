#!/usr/bin/env python
"""SessionStart hook: подложить в контекст файл-задание предыдущей сессии (handoff).

Ищет файл в текущем проекте (и вверх до корня репозитория) по списку принятых имён.
Если файла нет — печатает НИЧЕГО и выходит с кодом 0: в проектах без handoff хук
обязан быть невидимым, иначе его отключат.

Три решения, которые здесь не случайны:

* вывод кодируется `ensure_ascii=True` — консоль Windows отдаёт stdout в кодировке
  системы, и кириллица в JSON доехала бы мусором; escape-последовательности
  проходят любой канал одинаково;
* содержимое обрезается по размеру: задание бывает длинным, а хук занимает место в
  каждом контексте сессии. Обрезка помечается явно, чтобы обрезанный хвост не
  выглядел концом файла;
* рядом с текстом печатается возраст файла и напоминание сверить его с живым
  состоянием: задание описывает момент записи, а не «сейчас». Работать по нему как
  по факту — это ровно та ошибка, ради которой хук и написан.

Событие выбрано осознанно: `additionalContext` поддерживают `SessionStart`,
`UserPromptSubmit`, `PostToolUse`/`PostToolBatch` и `Stop`/`SubagentStop`, но НЕ
`PreCompact` — там схема отвергает такой ответ молча.
"""

from __future__ import annotations

import json
import os
import sys
import time

# Порядок = порядок предпочтения. Первый найденный выигрывает: два задания в одном
# проекте расходятся молча, и читать надо то, которое считается основным.
CANDIDATES = (
    "docs/_prompt-next-session.md",
    "docs/HANDOFF.md",
    "HANDOFF.md",
    "docs/_handoff.md",
    ".claude/handoff.md",
)

MAX_CHARS = 16000
MAX_LEVELS_UP = 4


def _stdin_payload() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _roots(cwd: str) -> list[str]:
    """Каталог сессии и его родители до корня репозитория включительно.

    Сессия часто открыта на подкаталоге (`apps/web`), а задание лежит в корне
    проекта, поэтому поиск идёт вверх. Подъём ограничен и останавливается на
    каталоге с `.git`: выше начинается чужое дерево, и чужой HANDOFF.md там не наш.
    """
    roots = []
    current = os.path.abspath(cwd)
    for _ in range(MAX_LEVELS_UP + 1):
        roots.append(current)
        if os.path.isdir(os.path.join(current, ".git")):
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return roots


def _find(cwd: str) -> str | None:
    for root in _roots(cwd):
        for name in CANDIDATES:
            path = os.path.join(root, *name.split("/"))
            if os.path.isfile(path):
                return path
    return None


def _age(path: str) -> str:
    try:
        days = (time.time() - os.path.getmtime(path)) / 86400
    except OSError:
        return "возраст неизвестен"
    if days < 1:
        return "обновлён сегодня"
    if days < 2:
        return "обновлён вчера"
    return "обновлён %d дн. назад" % int(days)


def main() -> None:
    payload = _stdin_payload()
    cwd = payload.get("cwd") or os.getcwd()
    trigger = payload.get("source") or payload.get("trigger") or ""

    path = _find(cwd)
    if not path:
        return

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return

    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS] + "\n\n…[обрезано хуком; полный текст — в файле]"

    rel = os.path.relpath(path, cwd) if path.startswith(os.path.abspath(cwd)) else path
    header = (
        "Задание предыдущей сессии (`%s`, %s). Это записка одному адресату — тебе, — "
        "а не документация проекта.\n"
        "Она описывает состояние НА МОМЕНТ ЗАПИСИ: прежде чем действовать по ней, "
        "сверься с живым состоянием (`git status`, `git log`, текущая ветка) и "
        "проверь на месте упомянутые файлы, ручки и настройки. Расхождение задания с "
        "репозиторием — это факт о прошлом, а не инструкция.\n"
    ) % (rel, _age(path))
    if trigger == "compact":
        header += (
            "Сессия только что сжала контекст: перечитай задание и переориентируйся "
            "по живому состоянию, прежде чем продолжать правки.\n"
        )

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": header + "\n---\n\n" + text,
        }
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Хук никогда не роняет старт сессии: молчание лучше, чем красная ошибка
        # в каждом запуске Claude Code из-за чужого нечитаемого файла.
        pass
