---
name: send-push-notification
description: Delivers a push notification to the user's device. Use when output_mode is "push" or when a background task completes and the result warrants immediate surfacing. Do not use when output_mode is "silent".
---

# send_push_notification

Sends a push notification to the user's Android device via the Flutter app.

## When to use

- `output_mode` is `push`
- A background task finishes with a result worth surfacing immediately
- SPEDA determines the user should be informed without them opening the app

## When not to use

- `output_mode` is `silent` — silent results are stored in DB only, no notification sent
- The user is actively in a conversation — stream the response directly instead

## Tool call

```json
{
  "title": "Short notification title (≤64 chars)",
  "body": "Notification body text.",
  "priority": "normal"
}
```

`priority`: `"low"` | `"normal"` | `"high"`. Default `"normal"`.

Returns `"delivered"` on success or an error message on failure.

## Note

FCM push delivery integration is pending configuration.
