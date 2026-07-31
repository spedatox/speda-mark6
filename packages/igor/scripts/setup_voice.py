"""
One-shot voice setup: paste the Azure key once, get playable samples back.

Prompts for the key without echoing it (it never reaches the terminal, the
shell history, or a command line), stores it through the same managed-override
file the desktop Configuration tab writes, then immediately proves it works by
listing the Turkish voices the key can reach and rendering one sample per voice.

    python scripts/setup_voice.py

Re-running it replaces the stored key, so it doubles as the rotation path.
"""

import asyncio
import getpass
import sys
from pathlib import Path

# A Turkish Windows console is cp1254 and raises on characters outside it. The
# voice names and sample text are ASCII, but the sample line the operator sees
# is not — force UTF-8 so this never dies formatting its own output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as app_config                    # noqa: E402
from app.config import Settings, write_managed_env      # noqa: E402

DEFAULT_REGION = "germanywestcentral"
SAMPLE = "Merhaba, ben SPEDA. Bugün iki toplantın var, ikisi de öğleden sonra."
OUT_DIR = Path(__file__).resolve().parent / "tts_samples"


async def main() -> int:
    print("Azure Speech -> portal -> your Speech resource -> Keys and Endpoint\n")

    key = getpass.getpass("KEY 1 (input hidden, paste + Enter): ").strip()
    if not key:
        print("!!  No key entered — nothing was changed.")
        return 1
    region = input(f"Region [{DEFAULT_REGION}]: ").strip() or DEFAULT_REGION

    write_managed_env({"AZURE_SPEECH_KEY": key, "AZURE_SPEECH_REGION": region})
    del key
    print(f"\nOK  Stored in {app_config._MANAGED_ENV}")

    # The module-level `settings` was built at import, before the key existed.
    # Rebuild it so this same process can verify what it just wrote.
    app_config.settings = Settings()
    from app.services import tts

    if not tts.configured():
        print("!!  Key did not load — check the file above.")
        return 1

    print(f"  region: {region}\n")
    print("Checking the key against Azure…")
    voices = await tts.list_voices()
    if not voices:
        print("!!  Azure rejected the credential.")
        print("  The usual cause is a region that does not match the key.")
        return 1

    turkish = [v for v in voices if (v.get("locale") or "").startswith("tr-")]
    print(f"OK  Credential valid — {len(voices)} voices reachable, {len(turkish)} Turkish\n")

    OUT_DIR.mkdir(exist_ok=True)
    failed = False
    for v in turkish or [{"name": tts.settings.tts_default_voice, "gender": "?"}]:
        name = v["name"]
        try:
            audio = await tts.synthesize(SAMPLE, name)
        except tts.TTSError as exc:
            print(f"!!  {name}: {exc}")
            failed = True
            continue
        path = OUT_DIR / f"{name}.mp3"
        path.write_bytes(audio)
        print(f"OK  {name:26} {v.get('gender','?'):7} {len(audio) // 1024:4} KB -> {path.name}")

    if failed:
        return 1
    print(f"\nSamples are in {OUT_DIR}")
    print("Play them, then tell me which voice you want as SPEDA's.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
