"""
End-to-end Azure Speech check.

Verifies the credential, lists the Turkish voices the key can actually reach,
and synthesizes one sample per voice so they can be compared by ear before a
voice is committed to a profile.

    python scripts/check_tts.py                    # default sample line
    python scripts/check_tts.py "kendi cümlen"

Reads AZURE_SPEECH_KEY / AZURE_SPEECH_REGION the same way the app does, so a
key set through the desktop Configuration tab is picked up with no extra steps.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings           # noqa: E402
from app.services import tts              # noqa: E402

SAMPLE = "Bugün iki toplantın var, ikisi de öğleden sonra. Kartından kırk euro çekilmiş."
OUT_DIR = Path(__file__).resolve().parent / "tts_samples"


async def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else SAMPLE

    if not tts.configured():
        print("!!  AZURE_SPEECH_KEY is not set.")
        print("  Set it in .env, or in the desktop app under Settings -> Configuration -> Voice Output.")
        return 1
    print(f"region : {settings.azure_speech_region}")
    print(f"format : {settings.tts_output_format}")

    voices = await tts.list_voices()
    if not voices:
        print("!!  Could not list voices — the key or region is probably wrong.")
        return 1
    turkish = [v for v in voices if (v.get("locale") or "").startswith("tr-")]
    print(f"voices : {len(voices)} reachable, {len(turkish)} Turkish")
    for v in turkish:
        print(f"         {v['name']:26} {v['gender']:7} {v['display']}")

    spoken = tts.strip_for_speech(text)
    print(f"\ntext   : {text!r}")
    print(f"spoken : {spoken!r}  ({len(spoken)} billable chars)")

    OUT_DIR.mkdir(exist_ok=True)
    targets = [v["name"] for v in turkish] or [settings.tts_default_voice]
    failed = False
    for name in targets:
        try:
            audio = await tts.synthesize(text, name)
        except tts.TTSError as exc:
            print(f"!!  {name}: {exc}")
            failed = True
            continue
        path = OUT_DIR / f"{name}.mp3"
        path.write_bytes(audio)
        print(f"OK  {name:26} {len(audio) // 1024:4} KB -> {path}")

    if not failed:
        print(f"\nPlay the files in {OUT_DIR} and pick the voice you want.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
