from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Iterable


ROOT = Path(".").resolve()
OUTPUT_FILE = ROOT / "PROJECT_CONTEXT.md"

IGNORE_DIRS = {
    ".git",
    ".idea",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
}

IMPORTANT_FILE_NAMES = {
    "main.py",
    "app.py",
    "settings.py",
    "config.py",
    "configs.py",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    ".env.example",
    ".env",
}

MAX_FILE_PREVIEW = 40


def should_ignore(path: Path) -> bool:
    return any(part in IGNORE_DIRS for part in path.parts)


def build_tree(root: Path, prefix: str = "") -> list[str]:
    lines: list[str] = []

    try:
        entries = sorted(
            [p for p in root.iterdir() if not should_ignore(p)],
            key=lambda p: (p.is_file(), p.name.lower())
        )
    except PermissionError:
        return lines

    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")

        if entry.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            lines.extend(build_tree(entry, prefix + extension))

    return lines


def find_files(root: Path, names: set[str]) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if should_ignore(path):
            continue
        if path.is_file() and path.name in names:
            found.append(path)
    return sorted(found)


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8-sig")
        except Exception:
            return ""
    except Exception:
        return ""


def parse_requirements(path: Path) -> list[str]:
    packages: list[str] = []
    text = read_text_safe(path)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        packages.append(line)
    return packages


def extract_pyproject_dependencies(path: Path) -> list[str]:
    text = read_text_safe(path)
    deps: list[str] = []

    in_project_deps = False
    in_poetry_deps = False

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if line == "[project]":
            in_project_deps = True
            in_poetry_deps = False
            continue
        if line == "[tool.poetry.dependencies]":
            in_poetry_deps = True
            in_project_deps = False
            continue
        if line.startswith("[") and line.endswith("]"):
            in_project_deps = False
            in_poetry_deps = False
            continue

        if in_project_deps and line.startswith("dependencies"):
            # грубий парсинг TOML-масиву
            deps.append(line)
        elif in_poetry_deps and "=" in line and not line.startswith("python"):
            deps.append(line)

    return deps


def detect_entrypoints(root: Path) -> list[tuple[Path, str]]:
    results: list[tuple[Path, str]] = []

    for path in root.rglob("*.py"):
        if should_ignore(path):
            continue

        text = read_text_safe(path)
        if not text.strip():
            continue

        rel = path.relative_to(root).as_posix()

        has_main_guard = 'if __name__ == "__main__":' in text
        has_uvicorn = "uvicorn.run(" in text or "FastAPI(" in text
        has_asyncio_run = "asyncio.run(" in text

        score_parts = []
        if has_main_guard:
            score_parts.append("main-guard")
        if has_uvicorn:
            score_parts.append("uvicorn/fastapi")
        if has_asyncio_run:
            score_parts.append("asyncio")

        if score_parts:
            results.append((path, ", ".join(score_parts)))

    return sorted(results, key=lambda x: x[0].as_posix())


def get_top_level_packages(root: Path) -> list[str]:
    packages: list[str] = []
    for item in root.iterdir():
        if should_ignore(item):
            continue
        if item.is_dir() and (item / "__init__.py").exists():
            packages.append(item.name)
    return sorted(packages)


def summarize_python_file(path: Path, root: Path) -> str:
    text = read_text_safe(path)
    if not text.strip():
        return "Не вдалося прочитати файл."

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "Python-файл, але AST не розібрався."

    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    async_funcs = [n.name for n in tree.body if isinstance(n, ast.AsyncFunctionDef)]

    parts = []
    if classes:
        parts.append(f"Класи: {', '.join(classes[:8])}")
    if funcs:
        parts.append(f"Функції: {', '.join(funcs[:8])}")
    if async_funcs:
        parts.append(f"Async-функції: {', '.join(async_funcs[:8])}")

    if not parts:
        parts.append("Модуль без явних top-level класів/функцій.")

    return " | ".join(parts)


def collect_key_python_files(root: Path) -> list[Path]:
    candidates: list[Path] = []

    for path in root.rglob("*.py"):
        if should_ignore(path):
            continue

        rel = path.relative_to(root).as_posix().lower()

        if any(part in rel for part in [
            "main.py",
            "app.py",
            "config.py",
            "configs.py",
            "settings.py",
            "router",
            "api",
            "bot",
            "service",
            "infra",
        ]):
            candidates.append(path)

    return sorted(candidates)[:30]


def make_markdown() -> str:
    lines: list[str] = []

    lines.append("# PROJECT_CONTEXT")
    lines.append("")
    lines.append("Автоматично згенерована чернетка контексту проєкту.")
    lines.append("Частину розділів потрібно перевірити і доповнити вручну.")
    lines.append("")

    lines.append("## 1. Корінь проєкту")
    lines.append("")
    lines.append(f"`{ROOT}`")
    lines.append("")

    lines.append("## 2. Дерево проєкту")
    lines.append("")
    lines.append("```text")
    lines.extend(build_tree(ROOT))
    lines.append("```")
    lines.append("")

    lines.append("## 3. Верхньорівневі Python-пакети")
    lines.append("")
    top_packages = get_top_level_packages(ROOT)
    if top_packages:
        for pkg in top_packages:
            lines.append(f"- `{pkg}`")
    else:
        lines.append("- Не знайдено top-level пакетів із `__init__.py`")
    lines.append("")

    lines.append("## 4. Важливі файли")
    lines.append("")
    important_files = find_files(ROOT, IMPORTANT_FILE_NAMES)
    if important_files:
        for path in important_files:
            lines.append(f"- `{path.relative_to(ROOT).as_posix()}`")
    else:
        lines.append("- Не знайдено")
    lines.append("")

    lines.append("## 5. Імовірні точки входу")
    lines.append("")
    entrypoints = detect_entrypoints(ROOT)
    if entrypoints:
        for path, reason in entrypoints:
            rel = path.relative_to(ROOT).as_posix()
            module = rel[:-3].replace("/", ".")
            lines.append(f"- `{rel}` — ознаки: {reason}")
            lines.append(f"  - Можливий запуск: `python -m {module}`")
    else:
        lines.append("- Не знайдено явних entrypoint-файлів")
    lines.append("")

    lines.append("## 6. Залежності")
    lines.append("")

    req_path = ROOT / "requirements.txt"
    pyproject_path = ROOT / "pyproject.toml"

    if req_path.exists():
        reqs = parse_requirements(req_path)
        lines.append("### requirements.txt")
        lines.append("")
        if reqs:
            for dep in reqs:
                lines.append(f"- `{dep}`")
        else:
            lines.append("- Порожньо або не вдалося прочитати")
        lines.append("")

    if pyproject_path.exists():
        pyproject_deps = extract_pyproject_dependencies(pyproject_path)
        lines.append("### pyproject.toml")
        lines.append("")
        if pyproject_deps:
            for dep in pyproject_deps:
                lines.append(f"- `{dep}`")
        else:
            lines.append("- Залежності не вдалося надійно витягнути автоматично")
        lines.append("")

    if not req_path.exists() and not pyproject_path.exists():
        lines.append("- Не знайдено `requirements.txt` або `pyproject.toml`")
        lines.append("")

    lines.append("## 7. Ключові Python-файли")
    lines.append("")
    for path in collect_key_python_files(ROOT):
        rel = path.relative_to(ROOT).as_posix()
        summary = summarize_python_file(path, ROOT)
        lines.append(f"- `{rel}`")
        lines.append(f"  - {summary}")
    lines.append("")

    lines.append("## 8. Що потрібно дописати вручну")
    lines.append("")
    lines.append("- Яка архітектура є фінальною: FastAPI / Telegram bot / workers / ML pipelines")
    lines.append("- Які модулі актуальні, а які deprecated")
    lines.append("- Які команди запуску є канонічними")
    lines.append("- Які папки не можна ламати або перейменовувати")
    lines.append("- Які змінні середовища обов'язкові")
    lines.append("- Які бізнес-правила важливі для подальшої генерації коду")
    lines.append("")

    lines.append("## 9. Нотатки")
    lines.append("")
    lines.append("Цей файл створено автоматично. Перед використанням як єдиного джерела правди його треба перевірити.")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    markdown = make_markdown()
    OUTPUT_FILE.write_text(markdown, encoding="utf-8")
    print(f"✅ Згенеровано: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()