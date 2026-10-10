import io
import re
from typing import List, Dict, Any, Optional

try:
    import pypdf
except ImportError:
    pypdf = None

# Reusing `services/attachments.py` logic where appropriate.

class ExtractedPassage:
    def __init__(self, content: str, page: Optional[int] = None, section: Optional[str] = None):
        self.content = content
        self.page = page
        self.section = section
        self.needs_review = False

def chunk_text(text: str, max_length: int = 2000) -> List[str]:
    """Very naive chunking by paragraphs/length."""
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) > max_length:
            if current:
                chunks.append(current.strip())
            current = p
        else:
            current += "\n\n" + p if current else p
    if current:
        chunks.append(current.strip())
    return chunks

def extract_document_bytes(blob: bytes, mime_type: str) -> List[ExtractedPassage]:
    """
    Extracts text from bytes based on mime type, yielding bounded passages.
    """
    passages = []
    
    if mime_type == "text/plain" or mime_type.startswith("text/"):
        text = blob.decode("utf-8", errors="replace")
        chunks = chunk_text(text)
        for chunk in chunks:
            passages.append(ExtractedPassage(content=chunk))
            
    elif mime_type == "application/pdf":
        if pypdf:
            try:
                reader = pypdf.PdfReader(io.BytesIO(blob))
                for page_num, page in enumerate(reader.pages, start=1):
                    page_text = page.extract_text() or ""
                    if not page_text.strip():
                        continue
                    # Chunk large pages to avoid giant single passages
                    chunks = chunk_text(page_text)
                    for chunk in chunks:
                        passages.append(ExtractedPassage(content=chunk, page=page_num))
            except Exception as e:
                p = ExtractedPassage(content=f"[PDF Extraction Failed: {e}]", page=1)
                p.needs_review = True
                passages.append(p)
        else:
            passages.append(ExtractedPassage(content="[PDF Content Extracted: pypdf not available]", page=1))
        
    elif mime_type in ["image/jpeg", "image/png"]:
        # Stub for OCR
        p = ExtractedPassage(content="[OCR Extracted Text from Image]")
        p.needs_review = True
        passages.append(p)
        
    else:
        # Fallback
        p = ExtractedPassage(content=f"Unsupported or binary format: {mime_type}")
        passages.append(p)

    return passages
