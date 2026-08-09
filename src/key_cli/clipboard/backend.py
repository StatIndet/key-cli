from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..utils.output import DEPENDENCY_FAILURE, GENERAL_FAILURE, Result, error, fail
from ..utils.executable import current_key_executable


MAX_PAYLOAD = 64 * 1024 * 1024
MAX_LIMIT = 500
CAPABILITIES = {"inspect": True, "preview": True, "mimeRestore": True, "mimeAwareStore": True}
FILE_MIME_TYPES = ("x-special/gnome-copied-files", "text/uri-list")
IMAGE_MIME_TYPES = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
)
PLAIN_TEXT_MIME_TYPES = ("text/plain;charset=utf-8", "text/plain")
HTML_MIME_TYPES = ("text/html", "text/html;charset=utf-8")
URI_FILE_SCHEMES = {
    "afc",
    "computer",
    "dav",
    "davs",
    "desktop",
    "file",
    "ftp",
    "gphoto2",
    "mtp",
    "network",
    "recent",
    "sftp",
    "smb",
    "trash",
}
GNOME_FILE_OPERATIONS = {"copy", "cut"}
CODE_FILE_EXTENSIONS = {
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".hpp",
    ".rs",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".lua",
    ".qml",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rb",
    ".php",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
}
CODE_FILE_NAMES = {"makefile", "cmakelists.txt", "dockerfile"}


def executable(name: str) -> str | None:
    return shutil.which(name)


def dependencies() -> dict[str, bool | None]:
    return {
        "cliphist": executable("cliphist") is not None,
        "wlCopy": executable("wl-copy") is not None,
        "wlPaste": executable("wl-paste") is not None,
    }


def watcher_lock_path() -> Path | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    return Path(runtime) / "key" / "clipboard-watch.lock" if runtime else None


def acquire_watcher_lock():
    lock_path = watcher_lock_path()
    if lock_path is None:
        raise OSError("XDG_RUNTIME_DIR is required for the clipboard watcher")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = lock_path.open("a+")
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Python opens files close-on-exec by default.  The lock must survive
        # the exec from key clipboard watch into wl-paste.
        os.set_inheritable(lock.fileno(), True)
    except Exception:
        lock.close()
        raise
    return lock


def watcher_running() -> bool:
    try:
        lock = acquire_watcher_lock()
    except BlockingIOError:
        return True
    except OSError:
        return False
    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    lock.close()
    return False


def run(program: str, arguments: list[str], input_data: bytes | None = None, timeout: float = 10) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run([program, *arguments], input=input_data, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_wl_copy(program: str, arguments: list[str], input_data: bytes) -> subprocess.CompletedProcess[bytes] | None:
    """Start a detached foreground wl-copy owner and verify startup.

    The default wl-copy mode forks before returning.  That is useful for a
    terminal command, but it makes a short-lived CLI wrapper observe the
    parent exit before the selection owner has published its offer.  The
    foreground mode gives us a process whose lifetime represents ownership of
    the selection; it must therefore be started with Popen and left running.
    """
    try:
        process = subprocess.Popen(
            [program, "--foreground", *arguments],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            if process.stdin is None:
                process.kill()
                process.wait()
                return None
            process.stdin.write(input_data)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            process.kill()
            process.wait()
            return None

        deadline = time.monotonic() + 0.25
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if process.returncode is None:
            # The foreground owner is intentionally detached from this short
            # lived CLI process.  Mark the Popen handle settled so its
            # destructor does not emit a false ResourceWarning on exit.
            process.returncode = 0
        return subprocess.CompletedProcess(
            [program, "--foreground", *arguments],
            process.returncode if process.returncode is not None else 0,
        )
    except OSError:
        return None


def base_entry(entry_id: str) -> dict:
    return {
        "id": entry_id,
        "payloadKind": "binary",
        "textSubtype": None,
        "icon": "data_object",
        "preview": "",
        "searchText": "",
        "previewUrl": "",
        "mimeType": "",
        "byteSize": 0,
        "width": 0,
        "height": 0,
        "fileCount": 0,
        "files": [],
        "fileOperation": None,
        "operation": None,
        "multiline": False,
        "lineCount": 0,
        "restorable": True,
    }


def text_display(value: str) -> tuple[str, str, int, bool]:
    lines = [line.replace("\t", " ").strip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [line for line in lines if line]
    if not lines:
        return "", "", 0, False
    return lines[0][:240], (lines[1] + ("…" if len(lines) > 2 else ""))[:300] if len(lines) > 1 else "", len(lines), len(lines) > 1


def image_info(data: bytes) -> tuple[str, int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return "image/png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        if len(data) >= 10:
            return "image/gif", int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        if data[12:16] == b"VP8X":
            return "image/webp", 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        return "image/webp", 0, 0
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                break
            size = int.from_bytes(data[index:index + 2], "big")
            if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0)):
                if index + 7 < len(data):
                    return "image/jpeg", int.from_bytes(data[index + 5:index + 7], "big"), int.from_bytes(data[index + 3:index + 5], "big")
                break
            index += max(size, 2)
    return None


def preview_path(entry_id: str, data: bytes, mime: str) -> str:
    root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "clavis" / "clipboard"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    suffix = "." + (mime.split("/", 1)[-1].replace("jpeg", "jpg") or "bin")
    path = root / f"entry-{entry_id}-{hashlib.sha256(data).hexdigest()[:16]}{suffix}"
    if not path.exists():
        path.write_bytes(data)
        os.chmod(path, 0o600)
    return path.resolve().as_uri()


def file_metadata(uri: str) -> dict:
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    path = (
        Path(unquote(parsed.path))
        if scheme == "file" and parsed.netloc in {"", "localhost"}
        else None
    )
    name = path.name if path else unquote(Path(parsed.path).name)
    if not name:
        name = parsed.netloc or scheme or uri

    exists = False
    readable = False
    directory = False
    byte_size = 0
    if path is not None:
        try:
            exists = path.exists()
            readable = os.access(path, os.R_OK)
            directory = path.is_dir()
            if path.is_file():
                byte_size = path.stat().st_size
        except OSError:
            pass

    value = {
        "uri": uri,
        "local": path is not None,
        "exists": exists,
        "readable": readable,
        "directory": directory,
        "byteSize": byte_size,
        "mimeType": mimetypes.guess_type(name, strict=False)[0] or "",
        "category": "file",
        "icon": "file_present",
        "previewUrl": "",
        "name": name,
        "parent": str(path.parent) if path else f"{scheme}://{parsed.netloc}",
    }
    is_code_file = (
        name.lower() in CODE_FILE_NAMES
        or Path(name).suffix.lower() in CODE_FILE_EXTENSIONS
    )
    if value["directory"]:
        value.update({"category": "folder", "icon": "folder", "mimeType": "inode/directory"})
    elif value["mimeType"].startswith("image/"):
        value.update({
            "category": "image",
            "icon": "image",
            "previewUrl": uri if value["local"] and value["exists"] else "",
        })
    elif value["mimeType"].startswith("video/"):
        value.update({"category": "video", "icon": "video_file"})
    elif value["mimeType"].startswith("audio/"):
        value.update({"category": "audio", "icon": "audio_file"})
    elif value["mimeType"] == "application/pdf":
        value.update({"category": "pdf", "icon": "picture_as_pdf"})
    elif is_code_file:
        value.update({"category": "code", "icon": "code"})
    elif value["mimeType"].startswith("text/"):
        value.update({"category": "document", "icon": "description"})
    return value


def select_mime(available_types: list[str]) -> str:
    """Choose a clipboard offer by semantic class, then by safe MIME order."""
    available = {value.strip() for value in available_types if value.strip()}

    # File-manager offers carry information that text/plain cannot reproduce.
    # Prefer GNOME's operation-bearing form when both file MIME types exist.
    for mime in FILE_MIME_TYPES:
        if mime in available:
            return mime
    for mime in IMAGE_MIME_TYPES:
        if mime in available:
            return mime
    for mime in HTML_MIME_TYPES:
        if mime in available:
            return mime
    for mime in PLAIN_TEXT_MIME_TYPES:
        if mime in available:
            return mime
    return ""


def parse_uri_list(text: str) -> tuple[str | None, list[str]]:
    """Parse URI-list syntax without treating filesystem-looking text as a URI."""
    raw_lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [line for line in raw_lines if line and not line.startswith("#")]
    operation = lines[0].lower() if lines and lines[0].lower() in GNOME_FILE_OPERATIONS else None
    uri_lines = lines[1:] if operation else lines
    if not uri_lines:
        return operation, []

    for uri in uri_lines:
        parsed = urlparse(uri)
        if parsed.scheme.lower() not in URI_FILE_SCHEMES or any(character.isspace() for character in uri):
            return None, []
    return operation, uri_lines


def file_list_icon(files: list[dict]) -> str:
    if len(files) <= 1:
        return str(files[0].get("icon") or "file_present") if files else "file_copy"
    return "file_copy"


HTML_IMAGE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*(['\"])(.*?)\1", re.I | re.S)
HTML_MARKUP = re.compile(
    r"^\s*(?:<!doctype\b|<!--|<\?xml\b|"
    r"<(?:html|head|body|title|meta|link|style|script|p|div|span|a|img|"
    r"br|hr|h[1-6]|ul|ol|li|table|thead|tbody|tr|td|th|form|input|"
    r"button|select|option|textarea|strong|em|b|i|u|pre|code|"
    r"blockquote|section|article|main|header|footer|nav|figure|"
    r"figcaption|video|audio|source)\b[^>]*>)",
    re.I | re.S,
)
DATA_IMAGE = re.compile(r"^data:(image/(?:png|jpeg|gif|webp));base64,(.+)$", re.I | re.S)


def _html_image_payload(entry_id: str, text: str, create_preview: bool, depth: int) -> tuple[dict | None, dict | None, bytes | None]:
    if depth >= 2:
        return None, None, None
    match = HTML_IMAGE.search(text)
    if not match:
        return None, None, None
    source = html.unescape(match.group(2)).strip()
    data_match = DATA_IMAGE.fullmatch(source)
    image_data: bytes | None = None
    if data_match:
        try:
            image_data = base64.b64decode(data_match.group(2), validate=True)
        except (ValueError, binascii.Error):
            image_data = None
    elif source.lower().startswith("file://"):
        parsed = urlparse(source)
        if parsed.netloc in {"", "localhost"}:
            path = Path(unquote(parsed.path))
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_PAYLOAD:
                    image_data = path.read_bytes()
            except OSError:
                image_data = None
    if not image_data or len(image_data) > MAX_PAYLOAD:
        return None, None, None
    payload, payload_error, _ = _inspect_payload(entry_id, image_data, create_preview, depth + 1)
    if payload_error or not payload or payload.get("payloadKind") != "image":
        return None, None, None
    payload["htmlImageFallback"] = True
    return payload, None, image_data


def _inspect_payload(entry_id: str, data: bytes, create_preview: bool = True, depth: int = 0) -> tuple[dict | None, dict | None, bytes | None]:
    if len(data) > MAX_PAYLOAD:
        return None, error("clipboard_payload_too_large", "clipboard payload exceeds the safe limit"), None
    result = base_entry(entry_id)
    result["byteSize"] = len(data)
    image = image_info(data)
    if image:
        mime, width, height = image
        if width > 16384 or height > 16384:
            return None, error("clipboard_image_decode_failed", "image dimensions exceed the safe limit"), None
        result.update({"payloadKind": "image", "icon": "image", "mimeType": mime, "width": width, "height": height})
        if create_preview:
            result["previewUrl"] = preview_path(entry_id, data, mime)
        return result, None, None
    try:
        text = data.decode("utf-8") if b"\0" not in data else ""
    except UnicodeDecodeError:
        text = ""
    if text and any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        text = ""
    if text:
        if HTML_MARKUP.match(text):
            embedded, embedded_error, restore_data = _html_image_payload(
                entry_id, text, create_preview, depth)
            if embedded_error or embedded:
                return embedded, embedded_error, restore_data
            plain = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", text, flags=re.I | re.S)
            plain = re.sub(r"<[^>]+>", " ", plain)
            plain = html.unescape(re.sub(r"\s+", " ", plain)).strip()
            _, _, lines, multiline = text_display(plain)
            result.update({"payloadKind": "text", "textSubtype": "html", "mimeType": "text/html", "preview": plain[:4096], "searchText": plain[:262144], "multiline": multiline, "lineCount": lines, "icon": "article"})
            return result, None, None
        stripped = text.strip()
        operation, urls = parse_uri_list(text)
        if urls:
            files = [file_metadata(uri) for uri in urls]
            result.update({
                "payloadKind": "file-list" if len(files) > 1 else "file",
                "mimeType": "x-special/gnome-copied-files" if operation else "text/uri-list",
                "files": files,
                "fileCount": len(files),
                "fileOperation": operation,
                "operation": operation,
                "icon": file_list_icon(files),
            })
            return result, None, None
        _, _, line_count, multiline = text_display(text)
        subtype = "url" if stripped.startswith(("http://", "https://")) and " " not in stripped else "plain"
        result.update({"payloadKind": "text", "textSubtype": subtype, "mimeType": "text/plain;charset=utf-8", "icon": "link" if subtype == "url" else "content_paste", "preview": text[:4096], "searchText": text[:262144], "multiline": multiline, "lineCount": line_count})
        return result, None, None
    result["mimeType"] = "application/octet-stream"
    return result, None, None


def inspect_payload(entry_id: str, data: bytes, create_preview: bool = True) -> tuple[dict | None, dict | None]:
    payload, payload_error, _ = _inspect_payload(entry_id, data, create_preview)
    return payload, payload_error


def lightweight(entry_id: str, preview: str) -> dict:
    result = base_entry(entry_id)
    image_marker = re.match(r"^\[\[ binary data ([0-9.]+ [A-Za-z]+) ([^ ]+) ([0-9]+)x([0-9]+) \]\]$", preview)
    if image_marker:
        fmt = image_marker.group(2).lower()
        result.update({"payloadKind": "image", "textSubtype": None, "icon": "image", "mimeType": "image/jpeg" if fmt == "jpg" else "image/" + fmt, "width": int(image_marker.group(3)), "height": int(image_marker.group(4))})
    else:
        operation, urls = parse_uri_list(preview)
        if urls:
            files = [file_metadata(uri) for uri in urls]
            result.update({
                "payloadKind": "file-list" if len(files) > 1 else "file",
                "textSubtype": None,
                "mimeType": "x-special/gnome-copied-files" if operation else "text/uri-list",
                "files": files,
                "fileCount": len(files),
                "fileOperation": operation,
                "operation": operation,
                "icon": file_list_icon(files),
            })
        else:
            _, _, count, multiline = text_display(preview)
            result.update({"payloadKind": "text", "textSubtype": "html" if HTML_MARKUP.match(preview) else "plain", "mimeType": "text/html" if HTML_MARKUP.match(preview) else "text/plain;charset=utf-8", "preview": preview if not HTML_MARKUP.match(preview) else "", "icon": "article" if HTML_MARKUP.match(preview) else "content_paste", "multiline": multiline, "lineCount": count})
    return result


def common_payload(command: str, **values) -> dict:
    return {"schemaVersion": 1, "command": command, "dependencies": dependencies(), "capabilities": CAPABILITIES, **values}


def run_command(args) -> Result:
    command = f"clipboard.{args.action}"
    deps = dependencies()
    cliphist = executable("cliphist")
    wl_copy = executable("wl-copy")
    if args.action == "watch":
        wl_paste = executable("wl-paste")
        if not cliphist or not wl_paste:
            return fail(command, DEPENDENCY_FAILURE, "clipboard_dependency_unavailable", "cliphist and wl-paste are required for the watcher", dependencies=deps)
        runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        if not runtime:
            return fail(command, GENERAL_FAILURE, "runtime_directory_unavailable", "XDG_RUNTIME_DIR is required for the clipboard watcher")
        lock = None
        try:
            lock = acquire_watcher_lock()
        except (BlockingIOError, OSError) as exc:
            if isinstance(exc, BlockingIOError):
                return fail(command, GENERAL_FAILURE, "clipboard_watcher_already_running", "a clipboard watcher is already active")
            return fail(command, GENERAL_FAILURE, "watcher_lock_failed", str(exc))
        key = current_key_executable()
        try:
            os.execv(wl_paste, [wl_paste, "--watch", key, "clipboard", "store"])
        finally:
            lock.close()
    if args.action == "store":
        return store(command, cliphist, deps)
    if args.action == "status":
        watching = watcher_running()
        available = bool(cliphist and wl_copy and watching)
        return Result(0 if available else DEPENDENCY_FAILURE, command, common_payload(command, available=available, canList=bool(cliphist), canRestore=bool(cliphist and wl_copy), watcherRunning=watching, error=None if available else error("cliphist_watcher_inactive" if cliphist and wl_copy else "clipboard_dependency_unavailable", "cliphist watcher is inactive" if cliphist and wl_copy else "cliphist and wl-copy are required")), "available" if available else "cliphist watcher is inactive", not available)
    if args.action == "list":
        if not cliphist:
            return Result(DEPENDENCY_FAILURE, command, common_payload(command, available=False, canList=False, canRestore=False, watcherRunning=False, entries=[], error=error("cliphist_unavailable", "cliphist is not installed")), "cliphist is not installed", True)
        process = run(cliphist, ["list"])
        if not process or process.returncode != 0:
            return fail(command, GENERAL_FAILURE, "cliphist_list_failed", "unable to read clipboard history", dependencies=deps, capabilities=CAPABILITIES, entries=[])
        entries = []
        limit = min(max(1, args.limit), MAX_LIMIT)
        for line in process.stdout.splitlines():
            if len(entries) >= limit or b"\t" not in line:
                continue
            raw_id, raw_preview = line.split(b"\t", 1)
            if raw_id.isdigit() and int(raw_id) > 0:
                entries.append(lightweight(raw_id.decode(), raw_preview.decode(errors="replace")))
        watching = watcher_running()
        inactive = not entries and not watching
        return Result(DEPENDENCY_FAILURE if inactive else 0, command, common_payload(command, available=bool(wl_copy), canList=True, canRestore=bool(wl_copy), watcherRunning=watching, entries=entries, error=error("cliphist_watcher_inactive", "cliphist watcher is inactive") if inactive else None), "cliphist watcher is inactive" if inactive else f"{len(entries)} clipboard entries", inactive)
    if args.action == "clear":
        if not cliphist:
            return fail(command, DEPENDENCY_FAILURE, "cliphist_unavailable", "cliphist is not installed", dependencies=deps)
        process = run(cliphist, ["wipe"])
        good = bool(process and process.returncode == 0)
        return Result(0 if good else GENERAL_FAILURE, command, common_payload(command, available=True, error=None if good else error("cliphist_clear_failed", "unable to clear clipboard history")), "Clipboard history cleared" if good else "unable to clear clipboard history", not good)
    if not cliphist:
        return fail(command, DEPENDENCY_FAILURE, "cliphist_unavailable", "cliphist is not installed", dependencies=deps)
    entry_id = str(args.id)
    if not entry_id.isdigit() or int(entry_id) <= 0:
        return fail(command, 2, "usage_error", "clipboard entry id must be a positive decimal integer")
    if args.action == "delete":
        process = run(cliphist, ["delete"], entry_id.encode())
        good = bool(process and process.returncode == 0)
        return Result(0 if good else GENERAL_FAILURE, command, common_payload(command, id=entry_id, available=True, error=None if good else error("cliphist_delete_failed", "unable to delete clipboard entry")), "Clipboard entry deleted" if good else "unable to delete clipboard entry", not good)
    if args.action == "inspect":
        process = run(cliphist, ["decode"], entry_id.encode())
        if not process or process.returncode != 0:
            return fail(command, GENERAL_FAILURE, "clipboard_inspect_failed", "unable to decode clipboard entry", id=entry_id, dependencies=deps)
        payload, payload_error = inspect_payload(entry_id, process.stdout, True)
        if payload_error:
            return Result(GENERAL_FAILURE, command, common_payload(command, id=entry_id, available=True, error=payload_error), payload_error["message"], True)
        return Result(0, command, common_payload(command, **payload, available=True, error=None), "Clipboard entry inspected")
    if args.action == "restore":
        if not wl_copy:
            return fail(command, DEPENDENCY_FAILURE, "wl_copy_unavailable", "wl-copy is not installed", id=entry_id, dependencies=deps)
        process = run(cliphist, ["decode"], entry_id.encode())
        if not process or process.returncode != 0:
            return fail(command, GENERAL_FAILURE, "cliphist_decode_failed", "unable to decode clipboard entry", id=entry_id, dependencies=deps)
        payload, payload_error, restore_data = _inspect_payload(entry_id, process.stdout, False)
        if payload_error:
            return Result(GENERAL_FAILURE, command, common_payload(command, id=entry_id, error=payload_error), payload_error["message"], True)
        mime = str(payload.get("mimeType") or "")
        copy_args = ["--type", mime] if mime else []
        copy = run_wl_copy(wl_copy, copy_args, restore_data if restore_data is not None else process.stdout)
        good = bool(copy and copy.returncode == 0)
        return Result(0 if good else GENERAL_FAILURE, command, common_payload(command, id=entry_id, payloadKind=payload.get("payloadKind"), mimeType=mime, available=True, error=None if good else error("wl_copy_failed", "unable to write clipboard entry")), "Clipboard entry restored" if good else "unable to write clipboard entry", not good)
    return fail("clipboard", 2, "usage_error", f"unknown clipboard action: {args.action}")


def store(command: str, cliphist: str | None, deps: dict) -> Result:
    wl_paste = executable("wl-paste")
    if not cliphist or not wl_paste:
        return fail(command, DEPENDENCY_FAILURE, "clipboard_dependency_unavailable", "cliphist and wl-paste are required", dependencies=deps)
    if os.environ.get("CLIPBOARD_STATE", "").lower() == "sensitive":
        return Result(0, command, common_payload(command, available=True, stored=False, skippedSensitive=True, error=None), "Sensitive clipboard entry skipped")
    types = run(wl_paste, ["--list-types"])
    available_types = (types.stdout.decode(errors="replace").splitlines() if types and types.returncode == 0 else [])
    selected = select_mime(available_types)
    if not selected:
        return fail(command, GENERAL_FAILURE, "clipboard_mime_unsupported", "clipboard selection has no supported MIME", dependencies=deps)
    selection = run(wl_paste, ["--type", selected])
    if not selection or selection.returncode != 0 or len(selection.stdout) > MAX_PAYLOAD:
        return fail(command, GENERAL_FAILURE, "clipboard_read_failed", "unable to read clipboard selection", dependencies=deps)
    saved = run(cliphist, ["store"], selection.stdout)
    good = bool(saved and saved.returncode == 0)
    return Result(0 if good else GENERAL_FAILURE, command, common_payload(command, available=True, stored=good, selectedMime=selected, error=None if good else error("cliphist_store_failed", "unable to store clipboard entry")), "Clipboard entry stored" if good else "unable to store clipboard entry", not good)
