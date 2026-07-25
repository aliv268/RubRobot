import os
import re
import json
import asyncio
import hashlib
from pathlib import Path

from rubpy import Client
from rubpy.bot import BotClient

from mutagen import File as MutagenFile
from mutagen.mp3 import MP3, HeaderNotFoundError
from mutagen.id3 import (
    ID3,
    APIC,
    TIT2,
    TPE1,
    TALB,
    TPE2,
    TCOM,
    TCON,
    TCOP,
    COMM,
    error,
)


# ============================================================
# CONFIG
# ============================================================

ACCOUNT_SESSION = "my_account"

# Put your real Rubika bot token here.
# The bot must be admin in @AhangBazar and have send permissions.
BOT_TOKEN = "CAAFIE0FZPOMVGVPOZERQQZYXAYELTELOZKTFKUYOWIUZADSSWHEGZZSHAZYTWAR"

MY_CHANNEL = "@AhangBazar"

SOURCE_CHANNELS = [
    "@mokhtalefmusic_com",
    "@iranimusic_ir",
    "@AHANGE_CLIP_LATI",
    "@ahangklipshoti",
    "@ahanglatee",
    "@CLlPMUSiC",
    "@Rimixll",
    "@Rub_Mu3ic",
    "@ahang_latim",
    "@CLlP_DIDANI5",
    "@REMlX_Gofliml",
    "@Ahng_Clip_Rubika",
    "@Music_OLDs",
    "@music",
    "@Music_Rubka",
]

HISTORY_PAGES_PER_CHANNEL = 50
HISTORY_PAGE_SIZE = 25
REQUEST_DELAY_SECONDS = 0.5
MAX_FILE_SIZE_BYTES = 1024 * 1024 * 1024

COVER_IMAGE_PATH = "ahang_bazar_logo.png"
MUSIC_TAG_TEXT = "Rub | @AhangBazar"

MEDIA_CAPTION = """🎬✨ آهنگ بازار 🎧🔥

🔼🔼 موزیک کامل بالاست 🔼🔼

🆔 @AhangBazar"""

MUSIC_CAPTION = """🔥 داغ‌ترین موزیک‌های روز در آهنگ بازار

🎧 با آهنگ بازار، همیشه به‌روز باش

🆔 @AhangBazar"""

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
COMPLETED_PAIRS_FILE = BASE_DIR / "completed_pairs.json"
SOURCE_CURSORS_FILE = BASE_DIR / "source_cursors.json"

DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# CLIENTS / RUNTIME STATE
# ============================================================

account = Client(ACCOUNT_SESSION)
bot = BotClient(BOT_TOKEN)

SOURCE_GUIDS = {}
pending_media = {}


# ============================================================
# JSON STATE
# ============================================================

def read_json(path, default):
    if not path.exists():
        return default

    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)

        if isinstance(value, type(default)):
            return value

    except Exception as exc:
        print(f"[STATE WARN] Could not read {path.name}: {repr(exc)}")

    return default


def write_json(path, value):
    try:
        with path.open("w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)

    except Exception as exc:
        print(f"[STATE ERROR] Could not save {path.name}: {repr(exc)}")


completed_pairs = read_json(COMPLETED_PAIRS_FILE, {})
source_cursors = read_json(SOURCE_CURSORS_FILE, {})


def save_completed_pairs():
    write_json(COMPLETED_PAIRS_FILE, completed_pairs)


def save_source_cursors():
    write_json(SOURCE_CURSORS_FILE, source_cursors)


# ============================================================
# GENERIC HELPERS
# ============================================================

def safe_get(obj, *keys, default=None):
    for key in keys:
        try:
            value = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        except Exception:
            value = None

        if value not in (None, "", False, 0):
            return value

    return default


def as_dict(obj):
    if isinstance(obj, dict):
        return obj

    for method_name in ("todict", "to_dict", "dict"):
        method = getattr(obj, method_name, None)

        if callable(method):
            try:
                value = method()

                if isinstance(value, dict):
                    return value
            except Exception:
                pass

    try:
        value = vars(obj)

        if isinstance(value, dict):
            return value
    except Exception:
        pass

    return {}


def has_real_value(value):
    if value is None:
        return False

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return value != 0

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0

    return True


def get_message_id(message):
    return safe_get(
        message,
        "message_id",
        "message_id_str",
        "id",
        default="unknown",
    )


def get_message_guid(message):
    return safe_get(
        message,
        "object_guid",
        "chat_guid",
        "channel_guid",
        default=None,
    )


def cleanup_file(filepath):
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass


def safe_filename(filename, fallback):
    if not filename:
        return fallback

    filename = str(filename).strip()
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)

    if len(filename) > 150:
        stem, extension = os.path.splitext(filename)
        filename = stem[:125] + extension[:20]

    return filename or fallback


def make_pair_key(source_label, media_id, music_id):
    raw = f"{source_label}|{media_id}|{music_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# MUSIC NAME CLEANUP
# ============================================================

def clean_music_name(file_name, source_label=""):
    """
    Keep the original song title.

    Remove only source identifiers at the end:
    - @SourceChannel
    - SourceChannel
    - [@SourceChannel]
    - (SourceChannel)
    - rubika.ir/SourceChannel
    - t.me/SourceChannel
    - common promotional suffixes
    """
    if not file_name:
        return "Unknown Song"

    original_name = Path(str(file_name)).stem.strip()
    cleaned = original_name

    source_username = source_label.strip().lstrip("@").lower()

    if source_username:
        cleaned = re.sub(
            rf"(?i)\s*[\[\(\{{]\s*@?{re.escape(source_username)}\s*[\]\)\}}]\s*$",
            "",
            cleaned,
        )

        cleaned = re.sub(
            rf"(?i)\s*[-|•_]+\s*@?{re.escape(source_username)}\s*$",
            "",
            cleaned,
        )

        cleaned = re.sub(
            rf"(?i)\s+@?{re.escape(source_username)}\s*$",
            "",
            cleaned,
        )

    # Remove trailing @channel name even if it differs from configured source.
    cleaned = re.sub(
        r"\s*[-|•_]+\s*@[\w_]+\s*$",
        "",
        cleaned,
    )

    cleaned = re.sub(
        r"\s*[\[\(\{]\s*@[\w_]+\s*[\]\)\}]\s*$",
        "",
        cleaned,
    )

    # Remove only trailing source links.
    cleaned = re.sub(
        r"(?i)\s*[-|•_]*\s*(?:https?://)?(?:rubika\.ir|t\.me)/[\w_]+\s*$",
        "",
        cleaned,
    )

    # Remove only common promotional endings.
    cleaned = re.sub(
        r"(?i)\s*[-|•_]*\s*"
        r"(?:join|channel|music channel|rubika music|"
        r"کانال آهنگ|کانال موزیک|کانال موسیقی|موزیک جدید)\s*$",
        "",
        cleaned,
    )

    # Normalize whitespace and leftover separators.
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s*[-|•_]+\s*$", "", cleaned)
    cleaned = cleaned.strip()

    return cleaned or original_name


# ============================================================
# CHANNEL RESOLUTION
# ============================================================

async def resolve_source_guid(source_label):
    if source_label in SOURCE_GUIDS:
        return SOURCE_GUIDS[source_label]

    username = source_label.strip().lstrip("@")

    print(f"[RESOLVE] ({source_label}) Resolving source channel...")

    try:
        try:
            response = await account.get_object_by_username(username)
        except TypeError:
            response = await account.get_object_by_username(username=username)

    except Exception as exc:
        print(f"[RESOLVE ERROR] ({source_label}) {repr(exc)}")
        return None

    raw = as_dict(response)

    object_guid = (
        safe_get(response, "object_guid", "channel_guid", "group_guid")
        or safe_get(raw, "object_guid", "channel_guid", "group_guid")
    )

    if not object_guid:
        for nested_name in (
            "channel",
            "chat",
            "group",
            "object",
            "data",
            "result",
        ):
            nested = raw.get(nested_name)
            nested_raw = as_dict(nested)

            object_guid = (
                safe_get(nested, "object_guid", "channel_guid", "group_guid")
                or safe_get(nested_raw, "object_guid", "channel_guid", "group_guid")
            )

            if object_guid:
                break

    if not object_guid:
        print(f"[RESOLVE ERROR] ({source_label}) object_guid was not found.")
        return None

    SOURCE_GUIDS[source_label] = str(object_guid)

    print(f"[RESOLVE OK] ({source_label}) object_guid={object_guid}")

    return str(object_guid)


async def resolve_all_sources():
    print("[RESOLVE] Resolving all source channels...")

    for source_label in SOURCE_CHANNELS:
        await resolve_source_guid(source_label)
        await asyncio.sleep(REQUEST_DELAY_SECONDS)

    print(
        f"[RESOLVE] Completed: {len(SOURCE_GUIDS)}/"
        f"{len(SOURCE_CHANNELS)} source channels resolved.\n"
    )


# ============================================================
# FORWARDED CURSORS
# ============================================================

def get_forwarded_from_data(message):
    raw = as_dict(message)

    original_update = (
        raw.get("original_update")
        or raw.get("_original_update")
        or getattr(message, "original_update", None)
    )

    original_raw = as_dict(original_update)

    original_message = (
        original_raw.get("message")
        or raw.get("message")
        or {}
    )

    original_message_raw = as_dict(original_message)

    forwarded_from = (
        original_message_raw.get("forwarded_from")
        or safe_get(original_message, "forwarded_from")
        or original_raw.get("forwarded_from")
        or raw.get("forwarded_from")
    )

    return as_dict(forwarded_from)


def find_forwarded_source_guid(message):
    forwarded_from = get_forwarded_from_data(message)

    return safe_get(
        forwarded_from,
        "object_guid",
        "channel_guid",
        "from_object_guid",
        "source_object_guid",
        default=None,
    )


def find_forwarded_message_id(message):
    forwarded_from = get_forwarded_from_data(message)

    return safe_get(
        forwarded_from,
        "message_id",
        "source_message_id",
        "original_message_id",
        "forwarded_message_id",
        default=None,
    )


async def collect_forwarded_cursor(message):
    source_guid = find_forwarded_source_guid(message)
    source_message_id = find_forwarded_message_id(message)

    if not source_guid or not source_message_id:
        return False

    source_label = None

    for label, resolved_guid in SOURCE_GUIDS.items():
        if str(resolved_guid) == str(source_guid):
            source_label = label
            break

    if not source_label:
        print(
            f"[CURSOR SKIP] Unconfigured source forwarded: "
            f"object_guid={source_guid}"
        )
        return False

    source_cursors[source_label] = str(source_message_id)
    save_source_cursors()

    print(
        f"[CURSOR SAVED] ({source_label}) "
        f"source_message_id={source_message_id}"
    )

    return True


# ============================================================
# MEDIA DETECTION
# ============================================================

def inspect_file_metadata(value):
    if value is None:
        return None, None

    raw = as_dict(value)

    mime_type = str(
        safe_get(
            value,
            "mime_type",
            "mime",
            "file_mime",
            default=safe_get(
                raw,
                "mime_type",
                "mime",
                "file_mime",
                default="",
            ),
        )
        or ""
    ).lower()

    file_name = str(
        safe_get(
            value,
            "file_name",
            "filename",
            "name",
            default=safe_get(
                raw,
                "file_name",
                "filename",
                "name",
                default="",
            ),
        )
        or ""
    ).lower()

    if (
        "audio" in mime_type
        or mime_type == "mp3"
        or "mpeg" in mime_type
        or file_name.endswith((".mp3", ".m4a", ".aac", ".ogg", ".wav", ".flac"))
    ):
        return "music", value

    if (
        "video" in mime_type
        or file_name.endswith((".mp4", ".mkv", ".avi", ".mov", ".webm"))
    ):
        return "video", value

    if (
        "image" in mime_type
        or file_name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
    ):
        return "photo", value

    if (
        safe_get(value, "file_id", "dc_id", "id")
        or safe_get(raw, "file_id", "dc_id", "id")
    ):
        return "file", value

    return None, None


def find_file_metadata_deep(message):
    fields = [
        ("music", "music"),
        ("audio", "music"),
        ("file_inline", "music"),
        ("fileinline", "music"),
        ("voice", "music"),
        ("photo", "photo"),
        ("image", "photo"),
        ("video", "video"),
        ("gif", "video"),
        ("file", "file"),
        ("document", "file"),
    ]

    for field_name, fallback_kind in fields:
        value = safe_get(message, field_name)

        if not has_real_value(value):
            continue

        kind, metadata = inspect_file_metadata(value)

        if kind == "file":
            kind = fallback_kind

        if kind:
            return kind, metadata

        if fallback_kind in ("music", "photo", "video"):
            return fallback_kind, value

    raw = as_dict(message)

    for field_name, fallback_kind in fields:
        value = raw.get(field_name)

        if not has_real_value(value):
            continue

        kind, metadata = inspect_file_metadata(value)

        if kind == "file":
            kind = fallback_kind

        if kind:
            return kind, metadata

        if fallback_kind in ("music", "photo", "video"):
            return fallback_kind, value

    return None, None


def get_file_name(metadata, message, kind):
    extensions = {
        "music": ".mp3",
        "photo": ".jpg",
        "video": ".mp4",
    }

    name = None

    for source in (
        metadata,
        as_dict(metadata),
        message,
        as_dict(message),
    ):
        name = safe_get(source, "file_name", "filename", "name")

        if name:
            break

    fallback = f"{kind}_{get_message_id(message)}{extensions.get(kind, '')}"
    name = safe_filename(name, fallback)

    if not os.path.splitext(name)[1]:
        name += extensions.get(kind, "")

    return name


# ============================================================
# MP3 METADATA / COVER / CLEAN TITLE
# ============================================================

async def edit_mp3_metadata(filepath, source_label=""):
    """
    Remove all old source metadata from MP3.

    Keep only the actual song title from the filename after cleaning
    source-channel information, then replace all MP3 metadata with
    AhangBazar branding and the local cover image.
    """

    def edit_sync():
        current_path = Path(filepath)

        try:
            audio = MutagenFile(str(current_path))

            if audio is None:
                return False, "Invalid audio file", str(current_path)

            if not isinstance(audio, MP3):
                return False, "Not a standard MP3", str(current_path)

            # Keep original music name, remove only source channel details.
            clean_title = clean_music_name(
                current_path.name,
                source_label,
            )

            # Rename the sent MP3 filename too.
            clean_file_name = safe_filename(
                f"{clean_title}.mp3",
                current_path.name,
            )

            clean_path = current_path.with_name(clean_file_name)

            if clean_path != current_path:
                counter = 1
                desired_path = clean_path

                while clean_path.exists():
                    clean_path = current_path.with_name(
                        f"{desired_path.stem}_{counter}{desired_path.suffix}"
                    )
                    counter += 1

                current_path.rename(clean_path)

            try:
                tags = ID3(str(clean_path))
            except error:
                tags = ID3()

            # Delete every metadata field that may contain source-channel data.
            for frame_name in (
                "TIT2",  # Title
                "TPE1",  # Artist
                "TALB",  # Album
                "TPE2",  # Album artist
                "TCOM",  # Composer
                "TCON",  # Genre
                "TCOP",  # Copyright
                "COMM",  # Comments
                "USLT",  # Lyrics
                "SYLT",  # Synced lyrics
                "APIC",  # Old cover image
                "WOAR",  # Artist URL
                "WOAF",  # File URL
                "WOAS",  # Source URL
                "WORS",  # Radio URL
                "WCOM",  # Commercial URL
                "WCOP",  # Copyright URL
                "WPAY",  # Payment URL
                "WPUB",  # Publisher URL
                "TXXX",  # Custom text fields
                "WXXX",  # Custom URL fields
            ):
                tags.delall(frame_name)

            # Title remains the original cleaned song name.
            tags.add(TIT2(encoding=3, text=clean_title))

            # All other metadata is rewritten as before.
            tags.add(TPE1(encoding=3, text=MUSIC_TAG_TEXT))
            tags.add(TALB(encoding=3, text=MUSIC_TAG_TEXT))
            tags.add(TPE2(encoding=3, text=MUSIC_TAG_TEXT))
            tags.add(TCOM(encoding=3, text=MUSIC_TAG_TEXT))
            tags.add(TCON(encoding=3, text="Music"))
            tags.add(TCOP(encoding=3, text=MUSIC_TAG_TEXT))

            tags.add(
                COMM(
                    encoding=3,
                    lang="eng",
                    desc="Comment",
                    text=MUSIC_TAG_TEXT,
                )
            )

            cover_path = Path(COVER_IMAGE_PATH)

            if cover_path.exists():
                mime_type = "image/png"

                if cover_path.suffix.lower() in (".jpg", ".jpeg"):
                    mime_type = "image/jpeg"

                with cover_path.open("rb") as image_file:
                    tags.add(
                        APIC(
                            encoding=3,
                            mime=mime_type,
                            type=3,
                            desc="Cover",
                            data=image_file.read(),
                        )
                    )

            tags.save(str(clean_path), v2_version=3)

            cover_status = (
                "Metadata cleaned and cover updated"
                if cover_path.exists()
                else "Metadata cleaned; cover image not found"
            )

            return (
                True,
                f"{cover_status} | title={clean_title}",
                str(clean_path),
            )

        except HeaderNotFoundError:
            return False, "Invalid MP3 header", str(current_path)

        except Exception as exc:
            return False, repr(exc), str(current_path)

    loop = asyncio.get_running_loop()

    return await loop.run_in_executor(None, edit_sync)


# ============================================================
# DOWNLOAD
# ============================================================

async def download_message_file(message, kind, source_label):
    """
    Download a source file safely.

    Network failures are retried automatically.
    If all attempts fail, returns None and the bot continues
    to the next pair without requiring a restart.
    """
    DOWNLOAD_RETRIES = 5
    RETRY_DELAYS = [3, 7, 15, 30, 60]

    message_id = get_message_id(message)

    detected_kind, metadata = find_file_metadata_deep(message)

    if detected_kind:
        kind = detected_kind

    filename = get_file_name(metadata, message, kind)
    output_path = DOWNLOADS_DIR / filename

    if output_path.exists():
        unique_id = hashlib.sha1(
            f"{source_label}|{message_id}|{filename}".encode("utf-8")
        ).hexdigest()[:10]

        output_path = (
            DOWNLOADS_DIR /
            f"{output_path.stem}_{unique_id}{output_path.suffix}"
        )

    if not getattr(message, "client", None):
        message.client = account

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        current_path = output_path

        print(
            f"[DOWNLOAD] ({source_label}) "
            f"message_id={message_id} type={kind} "
            f"attempt={attempt}/{DOWNLOAD_RETRIES} "
            f"file={current_path.name}"
        )

        try:
            cleanup_file(str(current_path))

            try:
                result = await message.download(save_as=str(current_path))
            except TypeError:
                result = await message.download(str(current_path))

            if isinstance(result, bytes):
                current_path.write_bytes(result)

            elif isinstance(result, str):
                returned_path = Path(result)

                if returned_path.exists():
                    current_path = returned_path

            if not current_path.exists():
                raise RuntimeError("Download completed but file was not saved")

            file_size = current_path.stat().st_size

            if file_size <= 0:
                raise RuntimeError("Downloaded file is zero bytes")

            if file_size > MAX_FILE_SIZE_BYTES:
                cleanup_file(str(current_path))
                print(
                    f"[DOWNLOAD SKIP] ({source_label}) "
                    f"message_id={message_id}; file exceeds size limit."
                )
                return None

            print(
                f"[DOWNLOAD OK] ({source_label}) "
                f"file={current_path.name} size={file_size} "
                f"attempt={attempt}"
            )

            return str(current_path)

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            cleanup_file(str(current_path))

            error_text = str(exc).replace("\n", " ").strip()

            print(
                f"[DOWNLOAD WARN] ({source_label}) "
                f"message_id={message_id} attempt={attempt}/"
                f"{DOWNLOAD_RETRIES}: {error_text}"
            )

            if attempt >= DOWNLOAD_RETRIES:
                print(
                    f"[DOWNLOAD SKIP] ({source_label}) "
                    f"message_id={message_id}; all attempts failed. "
                    "Continuing with the next pair."
                )
                return None

            wait_seconds = RETRY_DELAYS[attempt - 1]

            print(
                f"[DOWNLOAD RETRY] ({source_label}) "
                f"message_id={message_id}; retrying in {wait_seconds}s..."
            )

            await asyncio.sleep(wait_seconds)

    return None


# ============================================================
# SEND WITH BOT
# ============================================================

async def send_file_to_channel(filepath, media_type, caption, source_label):
    path = Path(filepath)
    filename = path.name

    rubpy_type = {
        "music": "Music",
        "photo": "Image",
        "video": "Video",
    }.get(media_type, "File")

    print(
        f"[SEND] ({source_label}) "
        f"type={rubpy_type} file={filename} target={MY_CHANNEL}"
    )

    try:
        await bot.send_file(
            chat_id=MY_CHANNEL,
            file=str(path),
            type=rubpy_type,
            file_name=filename,
            text=caption,
        )

        print(
            f"[SEND OK] ({source_label}) "
            f"type={rubpy_type} method=bot"
        )

        return True

    except Exception as exc:
        print(
            f"[SEND ERROR] ({source_label}) "
            f"type={rubpy_type} file={filename}: {repr(exc)}"
        )

        return False


# ============================================================
# PAIR PROCESSING
# ============================================================

async def process_pair(media_message, media_type, music_message, source_label):
    media_id = get_message_id(media_message)
    music_id = get_message_id(music_message)

    pair_key = make_pair_key(source_label, media_id, music_id)

    if pair_key in completed_pairs:
        print(
            f"[DEDUP] ({source_label}) "
            f"Already sent: media={media_id}, music={music_id}"
        )
        return True

    print(
        f"[PAIR] ({source_label}) "
        f"media={media_type}:{media_id} + music:{music_id}"
    )

    media_path = None
    music_path = None

    try:
        # Download both before sending anything.
        media_path = await download_message_file(
            media_message,
            media_type,
            source_label,
        )

        if not media_path:
            print(
                f"[PAIR FAILED] ({source_label}) "
                "Media download failed. Nothing was sent."
            )
            return False

        music_path = await download_message_file(
            music_message,
            "music",
            source_label,
        )

        if not music_path:
            print(
                f"[PAIR FAILED] ({source_label}) "
                "Music download failed. Nothing was sent."
            )
            return False

        edit_ok, edit_result, edited_music_path = await edit_mp3_metadata(
            music_path,
            source_label,
        )

        music_path = edited_music_path

        if edit_ok:
            print(f"[EDIT OK] ({source_label}) {edit_result}")
        else:
            print(
                f"[EDIT WARN] ({source_label}) "
                f"{edit_result}; original audio will be sent."
            )

        # Always send music first.
        music_sent = await send_file_to_channel(
            music_path,
            "music",
            MUSIC_CAPTION,
            source_label,
        )

        if not music_sent:
            print(
                f"[PAIR FAILED] ({source_label}) "
                "Music send failed. Media was not sent."
            )
            return False

        # Media is sent only after music succeeds.
        media_sent = await send_file_to_channel(
            media_path,
            media_type,
            MEDIA_CAPTION,
            source_label,
        )

        if not media_sent:
            print(
                f"[PAIR FAILED] ({source_label}) "
                "Music was sent but media send failed."
            )
            return False

        completed_pairs[pair_key] = {
            "source": source_label,
            "media_id": str(media_id),
            "music_id": str(music_id),
            "media_type": media_type,
            "status": "sent",
        }

        save_completed_pairs()

        print(
            f"[DONE] ({source_label}) "
            f"Pair sent successfully: media={media_id}, music={music_id}"
        )

        return True

    finally:
        cleanup_file(media_path)
        cleanup_file(music_path)


async def handle_media_message(message, source_label):
    message_id = get_message_id(message)
    chat_key = get_message_guid(message) or source_label

    kind, _ = find_file_metadata_deep(message)

    if kind not in ("photo", "video", "music"):
        if chat_key in pending_media:
            print(
                f"[PAIR RESET] ({source_label}) "
                f"message_id={message_id}; next message is not MP3."
            )

            pending_media.pop(chat_key, None)

        return

    print(
        f"[DETECT] ({source_label}) "
        f"message_id={message_id} type={kind}"
    )

    if kind in ("photo", "video"):
        pending_media[chat_key] = {
            "media_message": message,
            "media_type": kind,
            "media_id": message_id,
        }

        print(
            f"[PENDING] ({source_label}) "
            f"{kind} stored; checking next message."
        )

        return

    waiting = pending_media.get(chat_key)

    if not waiting:
        print(
            f"[SKIP] ({source_label}) "
            f"Music message_id={message_id} has no preceding image/video."
        )
        return

    await process_pair(
        waiting["media_message"],
        waiting["media_type"],
        message,
        source_label,
    )

    pending_media.pop(chat_key, None)


# ============================================================
# HISTORY RESPONSE PARSING
# ============================================================

def extract_messages(response):
    if isinstance(response, list):
        return response

    direct_messages = safe_get(response, "messages")

    if isinstance(direct_messages, list):
        return direct_messages

    raw = as_dict(response)

    for key in ("messages", "data", "result"):
        value = raw.get(key)

        if isinstance(value, list):
            return value

        nested = as_dict(value)
        nested_messages = nested.get("messages")

        if isinstance(nested_messages, list):
            return nested_messages

    return []


async def get_history_page(object_guid, max_id):
    try:
        return await account.get_messages(
            chat_id=object_guid,
            max_id=max_id,
            limit=HISTORY_PAGE_SIZE,
        )

    except TypeError:
        try:
            return await account.get_messages(
                object_guid,
                max_id,
                HISTORY_PAGE_SIZE,
            )

        except TypeError:
            return await account.get_messages(
                object_guid=object_guid,
                max_id=max_id,
                limit=HISTORY_PAGE_SIZE,
            )


# ============================================================
# HISTORY SCAN
# ============================================================

async def fetch_history():
    for source_label in SOURCE_CHANNELS:
        print(f"\n[HISTORY] Starting history scan for: {source_label}")

        object_guid = await resolve_source_guid(source_label)

        if not object_guid:
            print(
                f"[HISTORY ERROR] ({source_label}) "
                "Could not resolve channel GUID."
            )
            continue

        max_id = source_cursors.get(source_label)

        if not max_id:
            print(
                f"[HISTORY WAIT] ({source_label}) "
                "No cursor is saved yet. Forward one recent post from this "
                "source to Saved Messages."
            )
            continue

        print(
            f"[HISTORY] ({source_label}) "
            f"Starting cursor message_id={max_id}"
        )

        all_messages = []
        used_cursors = set()

        for page_number in range(1, HISTORY_PAGES_PER_CHANNEL + 1):
            print(
                f"[HISTORY] ({source_label}) "
                f"Fetching page {page_number} with max_id={max_id}..."
            )

            try:
                response = await get_history_page(object_guid, max_id)
                messages = extract_messages(response)

                if not messages:
                    print(
                        f"[HISTORY] ({source_label}) "
                        "No more history messages. Scan finished."
                    )
                    break

                print(
                    f"[HISTORY] ({source_label}) "
                    f"Received {len(messages)} messages on page {page_number}."
                )

                all_messages.extend(messages)

                oldest_message = messages[-1]
                next_max_id = get_message_id(oldest_message)

                if (
                    next_max_id == "unknown"
                    or str(next_max_id) == str(max_id)
                    or str(next_max_id) in used_cursors
                ):
                    print(
                        f"[HISTORY] ({source_label}) "
                        "Pagination cursor did not advance. Scan finished."
                    )
                    break

                used_cursors.add(str(next_max_id))
                max_id = str(next_max_id)

                await asyncio.sleep(REQUEST_DELAY_SECONDS)

            except Exception as exc:
                print(
                    f"[HISTORY ERROR] ({source_label}) "
                    f"page={page_number}: {repr(exc)}"
                )
                break

        unique_messages = {}

        for message in all_messages:
            message_id = get_message_id(message)

            if message_id != "unknown":
                unique_messages[str(message_id)] = message

        chronological_messages = list(unique_messages.values())
        chronological_messages.reverse()

        print(
            f"[HISTORY] ({source_label}) "
            f"Reviewing {len(chronological_messages)} unique messages "
            "in chronological order..."
        )

        for message in chronological_messages:
            try:
                if not getattr(message, "client", None):
                    message.client = account

                await handle_media_message(message, source_label)

            except Exception as exc:
                print(
                    f"[HISTORY ERROR] ({source_label}) "
                    f"message_id={get_message_id(message)}: {repr(exc)}"
                )

        pending_media.pop(object_guid, None)

        print(
            f"[HISTORY DONE] ({source_label}) "
            f"History processing complete: "
            f"{len(chronological_messages)} messages reviewed."
        )


# ============================================================
# LIVE UPDATES
# ============================================================

@account.on_message_updates()
async def on_new_message(update):
    try:
        message = update

        if not hasattr(message, "message_id"):
            message = safe_get(update, "new_message", "message")

        if not message:
            return

        is_forward = safe_get(
            message,
            "is_forward",
            "is_forwarded",
            default=False,
        )

        if is_forward:
            cursor_saved = await collect_forwarded_cursor(message)

            if not cursor_saved:
                print(
                    f"[FORWARD SKIP] message_id={get_message_id(message)} "
                    "Forward metadata did not match a configured source."
                )

        object_guid = get_message_guid(message)

        source_label = None

        for label, source_guid in SOURCE_GUIDS.items():
            if str(source_guid) == str(object_guid):
                source_label = label
                break

        if not source_label:
            return

        if not getattr(message, "client", None):
            message.client = account

        print(
            f"[LIVE] ({source_label}) "
            f"New message: message_id={get_message_id(message)}"
        )

        await handle_media_message(message, source_label)

    except Exception as exc:
        print(f"[LIVE ERROR] {repr(exc)}")


# ============================================================
# MAIN
# ============================================================

async def main():
    print("Channel grabber starting...")

    if (
        not BOT_TOKEN
        or BOT_TOKEN == "PUT_YOUR_REAL_BOT_TOKEN_HERE"
    ):
        print("[START ERROR] BOT_TOKEN is missing.")
        print("[START ERROR] Put your real bot token into BOT_TOKEN and run again.")
        return

    await account.start()
    print("[START] User account started.")

    try:
        await bot.start()
        print("[START] Bot client started.")

    except Exception as exc:
        print(f"[START ERROR] Bot startup failed: {repr(exc)}")
        return

    print(f"[START] Target channel: {MY_CHANNEL}")
    print(f"[START] Source channels: {', '.join(SOURCE_CHANNELS)}")
    print(f"[START] History pages per channel: {HISTORY_PAGES_PER_CHANNEL}")
    print(f"[START] Cover image: {COVER_IMAGE_PATH}\n")

    await resolve_all_sources()

    print(
        "[SETUP] For sources without cursors, forward one recent post "
        "to Saved Messages while this script remains open."
    )
    print("[SETUP] A successful cursor save prints [CURSOR SAVED].\n")

    await fetch_history()

    print(
        "\n[LIVE] History scan complete. "
        "Listening for new source posts and forwarded cursors...\n"
    )

    await account.run()


if __name__ == "__main__":
    asyncio.run(main())
