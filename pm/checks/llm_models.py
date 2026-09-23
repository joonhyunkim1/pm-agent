"""코드에서 쓰는 LLM 모델 ID를 모은다.

지금은 목록만 만든다. P3에서 이 목록을 모델 출시·지원 종료 정보와 대조해
"교체하면 얼마나 싸지고 품질은 어떤지" 평가하는 입력으로 쓴다.
.env는 비밀값이 있어서 읽지 않는다(.env.example은 읽는다).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .base import Check, Context, Outcome

_MODEL = (
    r"(?:gpt-\d[\w.\-]*|gpt-image-[\w.\-]+|gpt-realtime[\w.\-]*|chatgpt-[\w.\-]+"
    r"|o\d(?:-(?:mini|pro|preview|deep-research))*(?:-\d{4}-\d{2}-\d{2})?"
    r"|text-embedding-[\w\-]+|whisper-[\w\-]+|tts-[\w\-]+|dall-e-[23]|omni-moderation-[\w\-]+"
    r"|codex-[\w.\-]+|claude-[\w.\-]+|gemini-[\w.\-]+)"
)
_QUOTED = re.compile(r"""["'`](""" + _MODEL + r""")["'`]""")
_ASSIGN = re.compile(r"""(?im)^\s*[\w.\-]*model[\w.\-]*\s*[=:]\s*["']?(""" + _MODEL + r""")\b""")

_EXTS = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".dart", ".yaml", ".yml", ".toml", ".json"}
_NAMES = {".env.example", ".env.sample"}
_SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", ".next", "dist", "build", "__pycache__",
              ".dart_tool", "ios", "android", "macos", "linux", "windows", "web", "coverage", ".pytest_cache"}
_SKIP_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock"}
_MAX_BYTES = 512 * 1024


def find_models(text: str) -> set[str]:
    return set(_QUOTED.findall(text)) | set(_ASSIGN.findall(text))


def scan_tree(root: Path, exclude: list[str]) -> dict[str, list[str]]:
    excl = [e.rstrip("/") for e in exclude if not e.startswith(".env")]
    found: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
            and os.path.normpath(os.path.join(rel_dir, d)) not in excl
        ]
        for fn in filenames:
            if fn in _SKIP_FILES:
                continue
            if Path(fn).suffix not in _EXTS and fn not in _NAMES:
                continue
            fp = Path(dirpath) / fn
            try:
                if fp.stat().st_size > _MAX_BYTES:
                    continue
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for m in find_models(line):
                    locs = found.setdefault(m, [])
                    if len(locs) < 5:
                        locs.append(f"{os.path.relpath(fp, root)}:{i}")
    return found


class LlmModelsCheck(Check):
    id = "llm_models"
    label = "LLM 모델 사용 현황"

    def run(self, ctx: Context) -> Outcome:
        models = scan_tree(ctx.path, ctx.manifest.exclude)
        if not models:
            return Outcome.of("LLM 모델 사용 없음", [], {"models": {}})
        names = sorted(models)
        summary = ", ".join(names[:3]) + (f" 외 {len(names) - 3}종" if len(names) > 3 else "")
        return Outcome.of(summary, [], {"models": models})
