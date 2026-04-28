from __future__ import annotations

import os
import sys

from dotenv import load_dotenv


def project_root_from(current_file: str, *, levels_up: int) -> str:
    path = current_file
    for _ in range(levels_up):
        path = os.path.dirname(path)
    return os.path.abspath(path)


def ensure_on_sys_path(path: str) -> None:
    if path not in sys.path:
        sys.path.append(path)


def load_project_env(project_root: str) -> None:
    load_dotenv()
    env_path = os.path.join(project_root, "env")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=False)
