from rest_framework import serializers

from apps.common.validation import DEFAULT_MAX_FILE_SIZE, validate_pdf_file

MAX_FILES = 20
MAX_TOTAL_SIZE = 150 * 1024 * 1024  # 150 MB combined
MAX_TOTAL_PAGES = 3000


class MergePDFRequestSerializer(serializers.Serializer):
    """
    files: two or more PDFs, sent as repeated multipart fields under the
           same "files" key.
    order: optional 0-based permutation of `files`' indices, so a frontend
           can let the user drag-and-drop reorder without re-uploading.
           Defaults to upload order when omitted.
    """

    files = serializers.ListField(
        child=serializers.FileField(),
        allow_empty=False,
    )
    order = serializers.ListField(
        child=serializers.IntegerField(min_value=0),
        required=False,
        allow_empty=False,
    )

    def validate_files(self, value):
        if len(value) < 2:
            raise serializers.ValidationError(
                "At least 2 PDF files are required to merge."
            )

        if len(value) > MAX_FILES:
            raise serializers.ValidationError(
                f"At most {MAX_FILES} files can be merged at once."
            )

        total_size = sum(f.size for f in value)
        if total_size > MAX_TOTAL_SIZE:
            raise serializers.ValidationError(
                f"Combined file size ({total_size // (1024 * 1024)} MB) "
                f"exceeds the maximum of {MAX_TOTAL_SIZE // (1024 * 1024)} MB."
            )

        total_pages = 0
        for f in value:
            total_pages += validate_pdf_file(f, max_size=DEFAULT_MAX_FILE_SIZE)

        if total_pages > MAX_TOTAL_PAGES:
            raise serializers.ValidationError(
                f"Combined page count ({total_pages}) exceeds the maximum "
                f"of {MAX_TOTAL_PAGES}."
            )

        return value

    def validate(self, attrs):
        files = attrs["files"]
        order = attrs.get("order")

        if order is not None and sorted(order) != list(range(len(files))):
            raise serializers.ValidationError({
                "order": (
                    f"must be a permutation of file indices "
                    f"0..{len(files) - 1}."
                ),
            })

        return attrs
