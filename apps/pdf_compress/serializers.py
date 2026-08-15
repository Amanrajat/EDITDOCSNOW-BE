from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import CompressJob


class CompressPDFRequestSerializer(serializers.Serializer):
    """
    file:  a single PDF.
    level: one of CompressJob.Level - "high_quality", "recommended"
           (default), "high_compression", "maximum_compression".
    """

    file = serializers.FileField()
    level = serializers.ChoiceField(choices=CompressJob.Level.choices, default=CompressJob.Level.RECOMMENDED)

    def validate_file(self, value):
        validate_pdf_file(value)
        return value
