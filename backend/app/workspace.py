"""Bounded, read-only access to a single project directory."""

import os
import re
from pathlib import Path, PurePosixPath


IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".aws",
    ".ssh",
    ".gnupg",
    "secrets",
    "secret",
    "credentials",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".turbo",
    "dist",
    "build",
    "target",
    "coverage",
}
IGNORED_FILE_NAMES = {
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
}
IGNORED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db"}
MAX_FILE_BYTES = 512_000
MAX_LISTED_FILES = 250
MAX_SEARCH_MATCHES = 20
MAX_SEARCH_BYTES = 8_000_000
SECRET_TEXT_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|private[_-]?key)"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9/+=._-]{16,}",
        re.IGNORECASE,
    ),
)


class WorkspaceError(Exception):
    """A safe-to-display workspace access error."""


class Workspace:
    """Read project files while enforcing root and secret-file boundaries."""

    def __init__(
        self,
        root: str | Path,
        *,
        editor_file: str | None = None,
        editor_content: str | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise WorkspaceError("The selected workspace folder does not exist.")

        self._read_cache: dict[str, str] = {}
        if editor_file and editor_content is not None:
            normalized = self.normalize_path(editor_file)
            if len(editor_content.encode("utf-8")) > MAX_FILE_BYTES:
                raise WorkspaceError("The active editor file is too large for model context.")
            if not self.is_excluded_path(normalized) and not self.contains_sensitive_text(editor_content):
                self._read_cache[normalized] = editor_content

    def list_files(self, limit: int = MAX_LISTED_FILES) -> list[str]:
        """List safe, relative file paths under the workspace root."""
        paths: list[str] = []
        for current, directories, filenames in os.walk(self.root, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(
                name
                for name in directories
                if name not in IGNORED_DIRECTORIES
                and not name.startswith(".env")
                and not (current_path / name).is_symlink()
            )

            for filename in sorted(filenames):
                path = current_path / filename
                relative = path.relative_to(self.root).as_posix()
                if self._is_ignored(relative) or path.is_symlink():
                    continue
                paths.append(relative)
                if len(paths) >= min(limit, MAX_LISTED_FILES):
                    return paths
        return paths

    def read_file(self, relative_path: str) -> str:
        """Read a safe text file, preferring the current editor buffer."""
        relative = self.normalize_path(relative_path)
        if self._is_ignored(relative):
            raise WorkspaceError("That file is excluded from agent context.")
        if relative in self._read_cache:
            return self._read_cache[relative]

        path = self._resolve_path(relative)
        if not path.is_file():
            raise WorkspaceError("That file does not exist in the workspace.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceError("That file is too large to include in model context.")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise WorkspaceError("That file is not UTF-8 text and was skipped.") from error
        except OSError as error:
            raise WorkspaceError("The file could not be read.") from error
        if self.contains_sensitive_text(content):
            raise WorkspaceError("That file appears to contain a secret and was skipped.")
        self._read_cache[relative] = content
        return content

    def search_code(self, query: str) -> list[dict[str, str | int]]:
        """Search safe text files for a literal, case-insensitive string."""
        if not query.strip():
            raise WorkspaceError("Search text cannot be empty.")

        matches: list[dict[str, str | int]] = []
        lowered_query = query.casefold()
        scanned_bytes = 0
        for relative in self.list_files():
            try:
                scanned_bytes += (self.root / relative).stat().st_size
            except OSError:
                continue
            if scanned_bytes > MAX_SEARCH_BYTES:
                break
            try:
                content = self.read_file(relative)
            except WorkspaceError:
                continue

            for line_number, line in enumerate(content.splitlines(), start=1):
                if lowered_query in line.casefold():
                    matches.append(
                        {
                            "path": relative,
                            "line": line_number,
                            "text": line[:400],
                        }
                    )
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        return matches
        return matches

    def path_exists(self, relative_path: str) -> bool:
        """Return whether a safe relative file currently exists."""
        relative = self.normalize_path(relative_path)
        if self._is_ignored(relative):
            raise WorkspaceError("That file is excluded from agent context.")
        path = self._resolve_path(relative)
        if path.exists() and not path.is_file():
            raise WorkspaceError("The proposed path is not a file.")
        return path.is_file()

    def is_excluded_path(self, relative_path: str) -> bool:
        """Tell callers whether a workspace-relative path is denied as context."""
        relative = self.normalize_path(relative_path)
        if self._is_ignored(relative):
            return True
        try:
            resolved = (self.root / relative).resolve()
            return (
                not resolved.is_relative_to(self.root)
                or self._is_ignored(resolved.relative_to(self.root).as_posix())
            )
        except (OSError, RuntimeError):
            return True

    @staticmethod
    def contains_sensitive_text(content: str) -> bool:
        return any(pattern.search(content) for pattern in SECRET_TEXT_PATTERNS)

    def _resolve_path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise WorkspaceError("That path is outside the selected workspace.")
        if self._is_ignored(candidate.relative_to(self.root).as_posix()):
            raise WorkspaceError("That file is excluded from agent context.")
        return candidate

    @staticmethod
    def normalize_path(value: str) -> str:
        if not value or "\x00" in value:
            raise WorkspaceError("A workspace-relative file path is required.")
        normalized_input = value.replace("\\", "/")
        parsed = PurePosixPath(normalized_input)
        if (
            not parsed.parts
            or parsed.is_absolute()
            or any(part in {"", ".", ".."} for part in parsed.parts)
        ):
            raise WorkspaceError("Use a relative path inside the selected workspace.")
        return parsed.as_posix()

    @staticmethod
    def _is_ignored(relative: str) -> bool:
        parts = PurePosixPath(relative).parts
        if any(
            part.lower() in IGNORED_DIRECTORIES or part.lower().startswith(".env")
            for part in parts[:-1]
        ):
            return True
        name = parts[-1].lower()
        return (
            name.startswith(".env")
            or name in IGNORED_FILE_NAMES
            or name.endswith(tuple(IGNORED_SUFFIXES))
            or name in {"id_rsa", "id_ed25519", "known_hosts"}
        )
