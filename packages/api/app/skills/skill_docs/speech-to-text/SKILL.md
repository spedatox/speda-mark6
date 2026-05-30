---
name: speech-to-text
description: Transcribes audio files to text using the Whisper STT engine. Use when the user sends a voice message or uploads an audio file that needs transcription.
---

# speech_to_text

Transcribes an audio file using the local Whisper STT engine on Contabo.

## When to use

- User sends a voice message
- User uploads an audio file and asks for transcription or a response to its content

## When not to use

- Input is already text — no transcription needed

## Tool call

```json
{
  "audio_path": "/absolute/path/to/audio.wav",
  "language": "auto"
}
```

`language`: ISO 639-1 code (e.g. `"en"`, `"tr"`) or `"auto"` for detection.

Returns the transcribed text as a plain string.

## Note

Whisper STT integration is pending deployment on Contabo.
