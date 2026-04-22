import logging

from app.skills.base import Skill
from app.core.context import AgentContext

logger = logging.getLogger(__name__)


class DocumentsSkill(Skill):
    name = "generate_document"
    description = (
        "Generates a formatted document in PPTX, DOCX, or PDF format from structured content. "
        "Use this when the user requests a presentation, report, memo, or any formal written deliverable. "
        "Do not use this for plain text responses — only when a downloadable file format is explicitly needed. "
        "Returns the absolute file path to the generated document in /tmp/speda_outputs/."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "enum": ["pptx", "docx", "pdf"],
                "description": "Output format.",
            },
            "title": {"type": "string", "description": "Document title."},
            "content": {
                "type": "string",
                "description": "Document content in Markdown. For PPTX, use H2 headings as slide separators.",
            },
        },
        "required": ["format", "title", "content"],
    }

    async def execute(self, args: dict, context: AgentContext) -> str:
        # TODO: Implement PPTX (pptxgenjs), DOCX (docx Node.js), PDF (reportlab) toolchains.
        logger.info(
            "documents_execute",
            extra={"request_id": context.request_id, "format": args.get("format")},
        )
        return f"Document generation ({args.get('format', 'unknown')}) not yet implemented."
