---
name: text-to-speech
description: Converts text to spoken audio using the Kokoro TTS engine. Use when the user requests a spoken response, voice playback, or audio output. Do not use for silent or background tasks.
---

# text_to_speech

Synthesises speech from text using the local Kokoro TTS engine on Contabo.

## When to use

- User asks for a spoken/audio response: "read this aloud", "say that", "give me a voice version"
- `output_mode` is `respond` and audio output is appropriate

## When not to use

- `output_mode` is `push` or `silent`
- Background or automated tasks with no user-facing audio

## Tool call

```json
{
  "text": "The text to synthesise.",
  "voice": "default"
}
```

Returns the absolute path to a `.wav` file in `/tmp/speda_outputs/`.

## Note

Kokoro TTS integration is pending deployment on Contabo. The skill returns a pending message until configured.
