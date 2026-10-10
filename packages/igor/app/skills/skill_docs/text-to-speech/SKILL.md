---
name: text-to-speech
description: Converts text into a downloadable MP3 using a configured voice. Use for an explicit request for an audio file. Do not call it for routine voice-mode replies, which the transport already speaks. Returns a download card for the generated recording.
---

# text_to_speech

Synthesises speech with the configured ElevenLabs, Azure or OpenAI voice.
ElevenLabs references use `elevenlabs:eleven_v4:<voice_id>`; older saved
Multilingual v2 references retain their voice ID and resolve to v4.

## When to use

- User asks for a spoken/audio response: "read this aloud", "say that", "give me a voice version"
- The owner explicitly wants a recording they can download

## When not to use

- Routine voice-mode responses: the client speaks these automatically
- Background or automated tasks with no requested audio file

## Tool call

```json
{
  "text": "The text to synthesise.",
  "voice": "elevenlabs:eleven_v4:<voice_id>",
  "title": "Spoken summary"
}
```

The `voice` field is optional. Returns a confirmation and a downloadable MP3
card, with the temporary file stored under `/tmp/speda_outputs/`.

## Note

ElevenLabs requires `ELEVENLABS_API_KEY`. v4 supports Stability and Similarity
tuning; Style, Speed and Speaker Boost overrides are omitted for this model.
Live replies use the Text to Dialogue WebSocket and 24 kHz PCM; this file tool
uses the HTTP speech endpoint and MP3.
