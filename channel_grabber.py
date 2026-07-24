import os
import re
import json
import hashlib
import asyncio
import mutagen
from rubpy import Client, filters as acc_filters
from rubpy.bot import BotClient
from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3, HeaderNotFoundError
from mutagen.id3 import ID3, APIC, error


BOT_TOKEN = "CACFFE0SOFQTXIENZTEKKGFSLJYRSTSQLQOEXKJLSOQTDJTIPAWAHWKGQTMOOCUV"
COVER_IMAGE_PATH = "ahang_bazar_logo.png"
CHANNEL_CHAT_ID = "@AhangBazar"
TAG_NAME = "Rub | @AhangBazar"


SOURCE_CHANNELS = [
    "@Remix_NiC",
]


HISTORY_PAGES_PER_CHANNEL = 50


MUSIC_CAPTION = """🔥 داغ‌ترین موزیک‌های روز در آهنگ بازار


🆔 @AhangBazar"""


MEDIA_HASHTAGS = "#موزیک #آهنگ #اهنگ #music #موزیک_جدید #آهنگ_جدید #ریمیکس"


MEDIA_CAPTION = f"""🎬✨ آهنگ بازار 🎧🔥


⬆️⬆️ آهنگشو بالا برات فرستادیم 🎶
@AhangBazar


{MEDIA_HASHTAGS}"""


OUTPUT_DIR = "downloads"
os.makedirs(OUTPUT_DIR, exist_ok=True)


DEDUP_FILE = "sent_hashes.json"
COMPLETED_PAIRS_FILE = "completed_pairs.json"


MIN_FILE_SIZE_BYTES = 1024
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024


DOWNLOAD_TIMEOUT = 90
SEND_TIMEOUT = 90
MAX_DOWNLOAD_ATTEMPTS = 2
SEND_RETRIES = 1


FAST_FAIL_ERRORS = (
    "TransferEncodingError",
    "ConnectionResetError",
    "Not enough data",
    "forcibly closed by the remote host",
    "status 500",
    "Response payload is not completed",
)


account = Client("my_account")
bot = BotClient(BOT_TOKEN)


pending = {}
processed_ids = set()


EXT_BY_KIND = {
    "photo": ".jpg",
    "video": ".mp4",
    "music": ".mp3",
}



def load_json_set(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()



def save_json_set(path, data_set):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(list(data_set), f)
    except Exception as e:
        print(f"[SAVE ERROR] ({path}) {repr(e)}")



sent_hashes = load_json_set(DEDUP_FILE)
completed_pairs = load_json_set(COMPLETED_PAIRS_FILE)



def compute_file_hash(filepath):
    h = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        print("[HASH ERROR]", repr(e))
        return None



def safe_name(name):
    name = name or "file"
    return re.sub(r'[\\/:*?"<>|]+', "_", name)



def clean_music_title(title):
    if not title:
        return title
    title = title.strip()
    nik = r"ن[یي][کك]"
    music = r"م[وو]ز[یي][کك]"
    fa_pattern = nik + r"[\s\u200c]*" + music
    en_pattern = r"n[ií][ck][\s\u200c]*music"
    en_pattern2 = r"music[\s\u200c]*n[ií][ck]"
    seps = r"[|\u0640\u2014\u2013\-\s]*"
    title = re.sub(seps + fa_pattern, "", title, flags=re.IGNORECASE)
    title = re.sub(seps + en_pattern, "", title, flags=re.IGNORECASE)
    title = re.sub(seps + en_pattern2, "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip()
    for ch in ["|", "\u0640", "-", "—", "–"]:
        title = title.rstrip(ch).strip()
        title = title.lstrip(ch).strip()
    return title



async def retry_call(func, *args, retries=5, delay=2, backoff=True, **kwargs):
    last_error = None
    for i in range(retries):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_error = e
            err_text = str(e)
            is_retryable = (
                isinstance(e, (TimeoutError, asyncio.TimeoutError))
                or "500" in err_text
                or "502" in err_text
                or "503" in err_text
                or "504" in err_text
            )
            if is_retryable and i < retries - 1:
                wait_time = delay * (2 ** i) if backoff else delay
                print(f"[RETRY] Attempt {i+1}/{retries} failed ({err_text}). Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                raise last_error



async def run_bg(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))



def rename_only(filepath):
    base = os.path.splitext(os.path.basename(filepath))[0]
    clean_base = clean_music_title(base)
    if not clean_base:
        clean_base = base or "music"
    clean_fname = safe_name(clean_base) + ".mp3"
    clean_path = os.path.join(os.path.dirname(filepath), clean_fname)
    if clean_path != filepath:
        if os.path.exists(clean_path):
            os.remove(clean_path)
        os.rename(filepath, clean_path)
    return clean_path, clean_fname



def process_mp3(filepath):
    try:
        try:
            audio = EasyID3(filepath)
        except mutagen.id3.ID3NoHeaderError:
            audio = MutagenFile(filepath, easy=True)
            if audio is None:
                raise HeaderNotFoundError("Unsupported or invalid audio file")
            if audio.tags is None:
                try:
                    audio.add_tags()
                except Exception:
                    pass

        old_title = None
        try:
            old_title = audio.get("title", [None])[0]
        except Exception:
            pass

        if not old_title:
            old_title = os.path.splitext(os.path.basename(filepath))[0]

        clean_title = clean_music_title(old_title)

        try:
            audio["artist"] = [TAG_NAME]
            audio["album"] = [TAG_NAME]
            audio["title"] = [clean_title]
            audio.save()
        except Exception:
            pass

        try:
            if os.path.exists(COVER_IMAGE_PATH):
                audio_cover = MP3(filepath, ID3=ID3)
                try:
                    audio_cover.add_tags()
                except error:
                    pass
                audio_cover.tags.delall("APIC")
                with open(COVER_IMAGE_PATH, "rb") as img:
                    mime = "image/jpeg"
                    if COVER_IMAGE_PATH.lower().endswith(".png"):
                        mime = "image/png"
                    audio_cover.tags.add(
                        APIC(
                            encoding=3,
                            mime=mime,
                            type=3,
                            desc="Cover",
                            data=img.read()
                        )
                    )
                audio_cover.save()
        except HeaderNotFoundError:
            pass

        base = os.path.splitext(os.path.basename(filepath))[0]
        clean_base = clean_music_title(base)
        if not clean_base:
            clean_base = clean_title or base or "music"

        clean_fname = safe_name(clean_base) + ".mp3"
        clean_path = os.path.join(os.path.dirname(filepath), clean_fname)

        if clean_path != filepath:
            if os.path.exists(clean_path):
                os.remove(clean_path)
            os.rename(filepath, clean_path)

        return clean_path, clean_fname

    except HeaderNotFoundError:
        return rename_only(filepath)



def is_valid_file(filepath):
    if not filepath or not os.path.exists(filepath):
        return False

    size = os.path.getsize(filepath)

    if size < MIN_FILE_SIZE_BYTES:
        print(f"[VALIDATION FAILED] File too small ({size} bytes): {filepath}")
        return False

    if size > MAX_FILE_SIZE_BYTES:
        print(f"[VALIDATION FAILED] File too large ({size} bytes, limit={MAX_FILE_SIZE_BYTES}): {filepath}")
        return False

    return True



def remove_file_silent(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass



def get_pair_key(chat_key, music_message_id):
    return f"{chat_key}:{music_message_id}"



async def send_to_channel(filepath, filename, file_type, caption, source_label="", check_duplicate=True, metadata=None):
    if not is_valid_file(filepath):
        print(f"[SEND SKIPPED] ({source_label}) Invalid or empty/too-large file, not sending: {filepath}")
        return "invalid"

    file_hash = compute_file_hash(filepath)
    if check_duplicate and file_hash and file_hash in sent_hashes:
        print(f"[DUPLICATE SKIPPED] ({source_label}) File already sent before (hash={file_hash[:10]}...): {filename}")
        return "duplicate"

    async def _send_via_bot():
        kwargs = dict(
            chat_id=CHANNEL_CHAT_ID,
            file=filepath,
            file_name=filename,
            type=file_type,
            text=caption
        )
        return await asyncio.wait_for(
            retry_call(bot.send_file, retries=SEND_RETRIES, delay=1, backoff=False, **kwargs),
            timeout=SEND_TIMEOUT
        )

    async def _send_via_account():
        try:
            return await asyncio.wait_for(
                retry_call(
                    account.send_file,
                    CHANNEL_CHAT_ID,
                    filepath,
                    caption=caption,
                    file_name=filename,
                    retries=SEND_RETRIES,
                    delay=1,
                    backoff=False
                ),
                timeout=SEND_TIMEOUT
            )
        except TypeError:
            return await asyncio.wait_for(
                retry_call(
                    account.send_file,
                    CHANNEL_CHAT_ID,
                    filepath,
                    caption,
                    retries=SEND_RETRIES,
                    delay=1,
                    backoff=False
                ),
                timeout=SEND_TIMEOUT
            )

    try:
        print(f"[SEND] ({source_label}) Sending {file_type} '{filename}' ({os.path.getsize(filepath)} bytes) to channel...")
        try:
            await _send_via_bot()
        except Exception as e:
            print(f"[SEND WARNING] ({source_label}) bot.send_file failed -> {type(e).__name__}: {e!r}")
            print(f"[SEND WARNING] ({source_label}) Falling back to account.send_file...")
            await _send_via_account()

        print(f"[SEND OK] ({source_label}) {file_type} sent successfully: {filename}")

        if file_hash:
            sent_hashes.add(file_hash)
            save_json_set(DEDUP_FILE, sent_hashes)

        return "sent"

    except Exception as e:
        print(f"[SEND ERROR] ({source_label}) Failed to send {file_type}: {type(e).__name__}: {e!r}")
        return "failed"



def is_source_match(chat_username, chat_guid):
    for src in SOURCE_CHANNELS:
        s = src.lstrip("@")
        if s in str(chat_username or "") or s in str(chat_guid or ""):
            return True
    return False



def _safe_get(obj, name, default=None):
    try:
        if isinstance(obj, dict):
            value = obj.get(name, default)
        else:
            value = getattr(obj, name, default)
    except Exception:
        return default

    if callable(value):
        try:
            value = value()
        except Exception:
            return default

    return value



def _message_to_dict(msg):
    if isinstance(msg, dict):
        return msg

    try:
        data = msg.todict()
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    try:
        data = msg.jsonify()
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return {}



def _filled(value):
    if value in (None, False, "", 0, "0", [], {}, ()):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if callable(value):
        return False
    return True



def _get_from_sources(sources, *names, default=None):
    for src in sources:
        for name in names:
            value = _safe_get(src, name, None)
            if value is not None:
                return value
    return default



def _extract_file_meta(file_value):
    if not _filled(file_value):
        return "", ""

    if isinstance(file_value, dict):
        file_name = str(
            file_value.get("file_name", "")
            or file_value.get("name", "")
            or file_value.get("filename", "")
            or ""
        ).strip().lower()
        mime_type = str(
            file_value.get("mime", "")
            or file_value.get("mime_type", "")
            or ""
        ).strip().lower()
        return file_name, mime_type

    if isinstance(file_value, (int, float, bool, str, bytes, bytearray)):
        return "", ""

    file_name = str(
        getattr(file_value, "file_name", "")
        or getattr(file_value, "name", "")
        or getattr(file_value, "filename", "")
        or ""
    ).strip().lower()

    mime_type = str(
        getattr(file_value, "mime", "")
        or getattr(file_value, "mime_type", "")
        or ""
    ).strip().lower()

    return file_name, mime_type



def detect_kind(msg):
    data = _message_to_dict(msg)
    sources = [data, msg]

    msg_type = str(_get_from_sources(sources, "type", default="") or "").strip().lower()

    music_value = _get_from_sources(sources, "music", "replymusic")
    video_value = _get_from_sources(sources, "video", "replyvideo", "replyvideomessage")
    photo_value = _get_from_sources(sources, "photo", "replyphoto")

    if _filled(music_value) or msg_type == "music":
        return "music"

    if _filled(video_value) or msg_type == "video":
        return "video"

    if _filled(photo_value) or msg_type == "photo":
        return "photo"

    file_candidates = [
        _get_from_sources(sources, "fileinline"),
        _get_from_sources(sources, "file"),
        _get_from_sources(sources, "replydocument"),
    ]

    for file_value in file_candidates:
        file_name, mime_type = _extract_file_meta(file_value)

        if file_name.endswith(".mp3") or "audio" in mime_type:
            return "music"
        if file_name.endswith(".mp4") or "video" in mime_type:
            return "video"
        if file_name.endswith(".jpg") or file_name.endswith(".jpeg") or file_name.endswith(".png") or "image" in mime_type:
            return "photo"

    if msg_type == "text":
        return None

    mid = _get_from_sources(sources, "message_id", "messageid")
    print(
        f"[DEBUG KIND] mid={mid} "
        f"type={msg_type} "
        f"photo={repr(photo_value)[:80]} "
        f"video={repr(video_value)[:80]} "
        f"music={repr(music_value)[:80]} "
        f"file={repr(file_candidates[1])[:120]} "
        f"fileinline={repr(file_candidates[0])[:120]}"
    )
    return None



async def refresh_message(msg_or_update, chat_guid, source_label=""):
    mid = getattr(msg_or_update, "message_id", None)
    if not mid or not chat_guid:
        return msg_or_update

    try:
        fresh = await account.get_messages_by_id(chat_guid, [mid])
        if fresh:
            fresh_msg = fresh[0] if isinstance(fresh, list) else fresh
            if fresh_msg is not None:
                fresh_msg.client = account
                return fresh_msg
    except Exception as e:
        print(f"[REFRESH WARNING] ({source_label}) Could not refresh message {mid}, using original object: {repr(e)}")

    return msg_or_update



async def download_and_process(msg_or_update, kind, source_label="", chat_guid=None):
    mid = getattr(msg_or_update, "message_id", "unknown")
    print(f"[DOWNLOAD ATTEMPT] ({source_label}) kind={kind} message_id={mid}")

    ext = EXT_BY_KIND.get(kind, "")
    target_path = os.path.join(OUTPUT_DIR, f"{kind}_{mid}{ext}")
    last_error = None

    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        remove_file_silent(target_path)

        try:
            current_msg = msg_or_update

            if attempt > 1 and chat_guid:
                current_msg = await refresh_message(msg_or_update, chat_guid, source_label)

            if getattr(current_msg, "client", None) is None:
                current_msg.client = account

            result = await asyncio.wait_for(current_msg.download(), timeout=DOWNLOAD_TIMEOUT)

            downloaded_file = None

            if isinstance(result, (bytes, bytearray)):
                with open(target_path, "wb") as f:
                    f.write(result)
                downloaded_file = target_path
            elif isinstance(result, str):
                if os.path.exists(result):
                    downloaded_file = result
                elif os.path.exists(target_path):
                    downloaded_file = target_path
            elif os.path.exists(target_path):
                downloaded_file = target_path

            if not downloaded_file or not os.path.exists(downloaded_file):
                raise RuntimeError("Downloaded file path not available")

            size = os.path.getsize(downloaded_file)

            if size > MAX_FILE_SIZE_BYTES:
                print(f"[DOWNLOAD SKIPPED] ({source_label}) File too large ({size} bytes, limit={MAX_FILE_SIZE_BYTES}): {downloaded_file}")
                remove_file_silent(downloaded_file)
                return None, None

            if size < MIN_FILE_SIZE_BYTES:
                print(f"[DOWNLOAD SKIPPED] ({source_label}) File too small ({size} bytes): {downloaded_file}")
                remove_file_silent(downloaded_file)
                raise RuntimeError(f"Downloaded file too small: {size} bytes")

            if kind == "music":
                try:
                    downloaded_file, clean_fname = await run_bg(process_mp3, downloaded_file)

                    if not is_valid_file(downloaded_file):
                        remove_file_silent(downloaded_file)
                        raise RuntimeError("Processed music file invalid")

                    return downloaded_file, clean_fname
                except Exception as e:
                    print(f"[PROCESS ERROR] ({source_label}) Failed to process mp3 tags: {repr(e)}")
                    remove_file_silent(downloaded_file)
                    raise

            return downloaded_file, os.path.basename(downloaded_file)

        except Exception as e:
            last_error = e
            err_text = f"{type(e).__name__}: {e!r}"
            print(f"[DOWNLOAD ERROR] ({source_label}) Attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS} failed for {kind} message_id={mid}: {err_text}")
            remove_file_silent(target_path)

            error_text = str(e)
            fatal = any(marker in error_text for marker in FAST_FAIL_ERRORS) or any(marker in repr(e) for marker in FAST_FAIL_ERRORS)

            if fatal:
                print(f"[DOWNLOAD FAST-SKIP] ({source_label}) Fatal download error detected. Skipping immediately.")
                break

            if attempt < MAX_DOWNLOAD_ATTEMPTS:
                await asyncio.sleep(2)

    print(f"[DOWNLOAD FAILED] ({source_label}) Could not download valid {kind} file, skipping to next item. Last error: {repr(last_error)}")
    remove_file_silent(target_path)
    return None, None



async def handle_media_message(msg_or_update, chat_key, kind, source_label="", chat_guid=None):
    global pending

    if kind in ("photo", "video"):
        print(f"[DETECT] ({source_label}) Found a {kind}. Waiting for the immediately following message to be music...")
        pending[chat_key] = {"msg": msg_or_update, "kind": kind}
        return

    if kind == "music":
        prev = pending.get(chat_key)
        if not prev:
            print(f"[SKIP] ({source_label}) Music found with no immediately preceding photo/video -> treated as ad, skipped.")
            return

        music_mid = getattr(msg_or_update, "message_id", None)
        pair_key = get_pair_key(chat_key, music_mid)

        if pair_key in completed_pairs:
            print(f"[ALREADY SENT] ({source_label}) This pair (music_id={music_mid}) was already fully sent before -> skipping without downloading, moving to next.")
            pending.pop(chat_key, None)
            return

        print(f"[DETECT] ({source_label}) Music found right after a {prev['kind']}. Downloading media first, then music. Sending media first, then music...")

        media_path, media_name = await download_and_process(prev["msg"], prev["kind"], source_label, chat_guid)
        if not media_path:
            print(f"[PAIR SKIPPED] ({source_label}) Failed to download {prev['kind']} -> skipping pair.")
            pending.pop(chat_key, None)
            return

        music_path, music_name = await download_and_process(msg_or_update, "music", source_label, chat_guid)
        if not music_path:
            print(f"[PAIR SKIPPED] ({source_label}) Failed to download music -> skipping pair.")
            remove_file_silent(media_path)
            pending.pop(chat_key, None)
            return

        media_type = "Image" if prev["kind"] == "photo" else "Video"

        media_status = await send_to_channel(
            media_path,
            media_name,
            media_type,
            MEDIA_CAPTION,
            source_label,
            check_duplicate=True,
            metadata=None
        )
        remove_file_silent(media_path)

        if media_status == "failed":
            print(f"[PAIR SKIPPED] ({source_label}) Media send failed -> music will not be sent.")
            remove_file_silent(music_path)
            pending.pop(chat_key, None)
            return

        if media_status == "duplicate":
            print(f"[INFO] ({source_label}) Media was duplicate, continuing with music send...")

        music_status = await send_to_channel(
            music_path,
            music_name,
            "Music",
            MUSIC_CAPTION,
            source_label,
            check_duplicate=True,
            metadata=None
        )
        remove_file_silent(music_path)

        if music_status in ("sent", "duplicate"):
            completed_pairs.add(pair_key)
            save_json_set(COMPLETED_PAIRS_FILE, completed_pairs)
            print(f"[PAIR DONE] ({source_label}) Pair completed with music status={music_status}.")
        else:
            print(f"[PAIR NOT COMPLETE] ({source_label}) Music send failed, pair not stored in completed_pairs.")

        pending.pop(chat_key, None)
        return

    print(f"[SKIP] ({source_label}) Unrelated message (text/sticker/voice/etc) -> pending pair discarded.")
    pending.pop(chat_key, None)



@account.on_message_updates(acc_filters.is_channel)
async def grab_from_source(client, update):
    try:
        chat_username = getattr(update, "chat_username", None) or getattr(update, "author_username", None)
        chat_guid = getattr(update, "object_guid", None) or getattr(update, "chat_id", None)

        if not is_source_match(chat_username, chat_guid):
            return

        source_label = chat_username or chat_guid or "unknown"
        msg_id = getattr(update, "message_id", None)

        print(f"[LIVE] New message from source channel ({source_label}) - message_id={msg_id}")

        if msg_id and msg_id in processed_ids:
            print(f"[SKIP] ({source_label}) Message {msg_id} already processed.")
            return
        if msg_id:
            processed_ids.add(msg_id)

        chat_key = str(chat_guid or chat_username)
        kind = detect_kind(update)

        if kind in ("photo", "video", "music"):
            print(f"[LIVE] ({source_label}) Message type detected: {kind}")
            await handle_media_message(update, chat_key, kind, source_label, chat_guid)
        else:
            print(f"[LIVE] ({source_label}) Message has no relevant file -> ignored.")
            pending.pop(chat_key, None)

    except Exception as e:
        print("[GRABBER ERROR]", repr(e))



async def fetch_history():
    for src in SOURCE_CHANNELS:
        source_label = src
        try:
            print(f"\n[HISTORY] Starting history scan for source channel: {source_label}")
            chat_info = await account.get_object_by_username(src.lstrip("@"))
            chat_guid = getattr(chat_info, "object_guid", None) or getattr(chat_info, "chat_id", None)

            if not chat_guid:
                print(f"[HISTORY ERROR] ({source_label}) Could not resolve chat_guid.")
                continue

            chat_key = str(chat_guid)
            middle_id = "0"
            all_messages = []
            seen_page_signatures = set()

            for page in range(HISTORY_PAGES_PER_CHANNEL):
                print(f"[HISTORY] ({source_label}) Fetching page {page + 1}...")
                result = await account.get_messages_interval(chat_guid, middle_id)
                messages = result.get("messages") if isinstance(result, dict) else getattr(result, "messages", None)

                if not messages:
                    print(f"[HISTORY] ({source_label}) No more messages found.")
                    break

                print(f"[HISTORY] ({source_label}) Received {len(messages)} messages on this page.")

                def raw_mid(m):
                    return getattr(m, "message_id", None) or (m.get("message_id") if isinstance(m, dict) else None)

                first_id = raw_mid(messages[0])
                last_id = raw_mid(messages[-1])
                print(f"[HISTORY PAGE IDS] ({source_label}) first={first_id} last={last_id} middle_id={middle_id}")

                page_signature = (str(first_id), str(last_id), len(messages))
                if page_signature in seen_page_signatures:
                    print(f"[HISTORY STOP] ({source_label}) Repeated page detected, stopping pagination.")
                    break
                seen_page_signatures.add(page_signature)

                all_messages.extend(messages)

                last_msg = messages[-1]
                new_middle = raw_mid(last_msg)

                if not new_middle:
                    print(f"[HISTORY STOP] ({source_label}) new_middle is empty.")
                    break

                if str(new_middle) == str(middle_id):
                    print(f"[HISTORY STOP] ({source_label}) Cursor did not move.")
                    break

                middle_id = str(new_middle)

                if len(messages) < 25:
                    print(f"[HISTORY] ({source_label}) Last page reached ({len(messages)} messages).")
                    break

            def get_mid(m):
                mid = getattr(m, "message_id", None) or (m.get("message_id") if isinstance(m, dict) else None)
                try:
                    return int(mid)
                except (TypeError, ValueError):
                    return 0

            unique_messages = {}
            for m in all_messages:
                mid = get_mid(m)
                if mid:
                    unique_messages[mid] = m

            all_messages = sorted(unique_messages.values(), key=get_mid)
            print(f"[HISTORY] ({source_label}) Total {len(all_messages)} unique messages to review. Processing in chronological order...")

            for msg in all_messages:
                mid = get_mid(msg)

                if mid in processed_ids:
                    continue

                processed_ids.add(mid)

                kind = detect_kind(msg)
                if kind in ("photo", "video", "music"):
                    print(f"[HISTORY] ({source_label}) message_id={mid} -> type: {kind}")
                    await handle_media_message(msg, chat_key, kind, source_label, chat_guid)
                else:
                    msg_type = getattr(msg, "type", None) if not isinstance(msg, dict) else msg.get("type")
                    print(f"[HISTORY UNKNOWN] ({source_label}) message_id={mid} -> unrecognized type={msg_type}")
                    pending.pop(chat_key, None)

            print(f"[HISTORY DONE] ({source_label}) History processing complete: {len(all_messages)} messages reviewed.\n")

        except Exception as e:
            print(f"[HISTORY ERROR] ({source_label}): {repr(e)}")



async def main():
    await account.start()
    await fetch_history()
    await account.run()



if __name__ == "__main__":
    print("Channel grabber running (history + live)...")
    asyncio.run(main())