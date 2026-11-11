"""Bounded UTF-8 reads from the Hisar download endpoint."""

import codecs


async def read_text_page(client, base_url, path, headers, offset, limit):
    headers = {**headers, "Range": f"bytes={offset}-{offset + limit}", "Accept-Encoding": "identity"}
    params = {"path": path, "inline": True}
    page = bytearray()
    async with client.stream("GET", f"{base_url}/files/download", params=params, headers=headers) as response:
        if response.status_code == 416 and offset == 0 and response.headers.get("content-range") == "bytes */0":
            return "", None
        if response.is_error:
            await response.aread()
        response.raise_for_status()
        # Older Hisar versions may ignore Range. Discard the prefix as it arrives.
        skip = offset if response.status_code == 200 else 0
        if response.status_code == 206 and not response.headers.get("content-range", "").startswith(f"bytes {offset}-"):
            raise ValueError("Hisar returned a different byte range")
        async for chunk in response.aiter_bytes(chunk_size=8192):
            if skip:
                discarded = min(skip, len(chunk))
                skip -= discarded
                chunk = chunk[discarded:]
            page.extend(chunk[:limit + 1 - len(page)])
            if len(page) > limit:
                break
        if skip:
            raise ValueError("Offset is past the end of the file")

    more = len(page) > limit
    raw = page[:limit]
    decoder = codecs.getincrementaldecoder("utf-8")()
    text = decoder.decode(raw, final=not more)
    if "\x00" in text:
        raise UnicodeError("File contains binary data")
    next_offset = offset + len(raw) - len(decoder.getstate()[0]) if more else None
    return text, next_offset
