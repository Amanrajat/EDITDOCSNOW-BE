import fitz

from .models import Document, DocumentBlock
from .pdf_extractor import extract_pdf_blocks


class DocumentService:

    @staticmethod
    def update_document_metadata(document):
        pdf = fitz.open(document.original_file.path)

        try:
            document.total_pages = len(pdf)

            document.save(
                update_fields=["total_pages"]
            )

            return document

        finally:
            pdf.close()


class BlockExtractionService:

    @staticmethod
    def extract_and_store(document):

        DocumentBlock.objects.filter(
            document=document
        ).delete()

        blocks = extract_pdf_blocks(
            document.original_file.path
        )

        block_instances = []

        for block in blocks:
            block_instances.append(
                DocumentBlock(
                    document=document,
                    page_number=block["page"],
                    text=block["text"],
                    original_text=block["text"],
                    bbox=block["bbox"],
                    font_name=block.get("font", ""),
                    font_size=block.get("size", 12),
                    color=block.get("color", "#000000"),
                    is_bold=block.get("bold", False),
                    is_italic=block.get("italic", False),
                    has_link=block.get("has_link", False),
                )
            )

        DocumentBlock.objects.bulk_create(
            block_instances
        )

        return document.blocks.all()


class BlockUpdateService:

    @staticmethod
    def update_blocks(document, blocks_data):

        updated_blocks = []

        existing_blocks = {
            str(block.id): block
            for block in DocumentBlock.objects.filter(
                document=document
            )
        }

        for block_data in blocks_data:

            block = existing_blocks.get(
                str(block_data["id"])
            )

            if not block:
                continue

            block.text = block_data["text"]

            updated_blocks.append(block)

        if updated_blocks:
            DocumentBlock.objects.bulk_update(
                updated_blocks,
                ["text"]
            )

        return updated_blocks