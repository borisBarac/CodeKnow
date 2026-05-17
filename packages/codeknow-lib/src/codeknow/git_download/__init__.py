"""Git repository downloader — clone or update repos for pipeline input."""

from .downloader import download, is_cloned
from .repo_map import (
    DEFAULT_STORE_PATH,
    get_path,
    get_url,
    list_all,
    load,
    register,
    save,
)

__all__ = [
    "DEFAULT_STORE_PATH",
    "download",
    "get_path",
    "get_url",
    "is_cloned",
    "list_all",
    "load",
    "register",
    "save",
]
