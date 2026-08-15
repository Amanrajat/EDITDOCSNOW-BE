import secrets

from django.db import migrations


def backfill_owner_tokens(apps, schema_editor):
    """
    Every Document created before owner_token existed has an empty token,
    which would make it permanently inaccessible once ownership checks are
    enforced (an empty provided token can never match, by design - see
    apps.common.ownership.is_owner's docstring on why blank-vs-blank must
    never match). Generate a real token for each so existing documents
    remain reachable - their owner just won't have it (no email/session to
    deliver it through), but this project has no such delivery mechanism
    for anyone yet, so this only preserves the status quo rather than
    granting or revoking access to anyone.
    """
    Document = apps.get_model("docs_editor", "Document")
    for document in Document.objects.filter(owner_token="").iterator():
        document.owner_token = secrets.token_urlsafe(32)
        document.save(update_fields=["owner_token"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("docs_editor", "0003_document_owner_token_documentobject"),
    ]

    operations = [
        migrations.RunPython(backfill_owner_tokens, noop_reverse),
    ]
