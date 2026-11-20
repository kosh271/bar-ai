"""Launch the local web workshop with ``python -m app``."""

from pathlib import Path

from .studio_server import main

APP_ROOT = Path(__file__).resolve().parent.parent


def __getattr__(name: str):
    if name == "Editor":
        from .legacy_gui import Editor

        return Editor
    raise AttributeError(name)


if __name__ == "__main__":
    main()
