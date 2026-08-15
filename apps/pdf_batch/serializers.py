from rest_framework import serializers

from apps.pdf_compress.models import CompressJob

from .services import MAX_BATCH_FILES


class BatchCompressRequestSerializer(serializers.Serializer):
    """
    files: one or more PDFs, sent as repeated multipart fields under the
           same "files" key. Unlike Merge's `files`, each one here is
           validated (and can fail) independently - a bad file doesn't
           reject the whole batch.
    level: one of CompressJob.Level, applied to every file in the batch.
    """

    files = serializers.ListField(
        child=serializers.FileField(),
        allow_empty=False,
    )
    level = serializers.ChoiceField(choices=CompressJob.Level.choices, default=CompressJob.Level.RECOMMENDED)

    def validate_files(self, value):
        if len(value) > MAX_BATCH_FILES:
            raise serializers.ValidationError(
                f"At most {MAX_BATCH_FILES} files can be processed in one batch."
            )
        return value
