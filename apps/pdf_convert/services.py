from django.core.files.base import ContentFile

from apps.common.ownership import generate_owner_token

from .models import ConversionJob


def _finalize(job, produce_fn, output_ext, output_is_zip):
    """Runs `produce_fn() -> (output_bytes, metadata dict)` and persists
    the result (or the failure reason) onto an already-created job."""
    try:
        output_bytes, metadata = produce_fn()
    except Exception as exc:
        job.status = ConversionJob.Status.FAILED
        job.error_message = str(exc)
        job.save(update_fields=["status", "error_message"])
        return job

    # A converter whose output shape depends on its own input (e.g.
    # PDF-to-JPG: one file for a single page, a ZIP for several) can
    # report that via "_output_ext"/"_is_zip" in its metadata dict - both
    # are popped off before saving so they never leak into the API response.
    resolved_ext = metadata.pop("_output_ext", output_ext)
    resolved_is_zip = metadata.pop("_is_zip", output_is_zip)

    job.output_file.save(f"{job.id}.{resolved_ext}", ContentFile(output_bytes), save=False)
    job.output_is_zip = resolved_is_zip
    job.metadata = metadata
    job.status = ConversionJob.Status.COMPLETED
    job.save(update_fields=["output_file", "output_is_zip", "metadata", "status"])

    return job


def run_conversion(user, uploaded_file, operation, converter_fn, output_ext, output_is_zip=False):
    """
    Shared orchestration for every single-file conversion in this app:
    create a ConversionJob row, run `converter_fn(file_bytes) ->
    (output_bytes, metadata dict)`, persist the result, and return the
    job. Every PDF-to-X converter (one PDF in) plugs into this.
    """
    job = ConversionJob.objects.create(
        user=user,
        owner_token=generate_owner_token(),
        operation=operation,
        source_filename=getattr(uploaded_file, "name", ""),
    )

    def produce():
        uploaded_file.seek(0)
        return converter_fn(uploaded_file.read())

    return _finalize(job, produce, output_ext, output_is_zip)


def run_conversion_multi(user, uploaded_files, operation, converter_fn, output_ext, output_is_zip=False):
    """
    Same lifecycle as run_conversion, but for operations that take
    *multiple* source files (e.g. JPG-to-PDF: several images -> one PDF).
    `converter_fn` takes no arguments - the caller already bound whatever
    per-file data it needs into a closure, since the shape of "the files"
    varies per operation (JPG-to-PDF needs ordered image bytes; other
    multi-file operations might need something else).
    """
    names = [getattr(f, "name", "") for f in uploaded_files]
    source_filename = f"{len(names)} files" if len(names) > 1 else (names[0] if names else "")

    job = ConversionJob.objects.create(
        user=user,
        owner_token=generate_owner_token(),
        operation=operation,
        source_filename=source_filename,
    )

    return _finalize(job, converter_fn, output_ext, output_is_zip)


def run_conversion_no_file(user, source_description, operation, converter_fn, output_ext, output_is_zip=False):
    """
    Same lifecycle again, for operations with no uploaded file at all
    (HTML-to-PDF: a URL or a raw HTML string). `converter_fn` takes no
    arguments, same as run_conversion_multi.
    """
    job = ConversionJob.objects.create(
        user=user,
        owner_token=generate_owner_token(),
        operation=operation,
        source_filename=source_description,
    )

    return _finalize(job, converter_fn, output_ext, output_is_zip)
