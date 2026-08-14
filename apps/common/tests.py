from types import SimpleNamespace

import fitz
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers

from .ownership import generate_owner_token, is_owner
from .validation import validate_pdf_file

User = get_user_model()


def _request(user=None, token=None, header_token=None):
    """Minimal stand-in for a DRF Request exposing what is_owner() reads."""
    return SimpleNamespace(
        user=user or AnonymousUser(),
        query_params={"token": token} if token else {},
        META={"HTTP_X_OWNER_TOKEN": header_token} if header_token else {},
    )


def _job(user=None, owner_token=""):
    return SimpleNamespace(user=user, user_id=user.id if user else None, owner_token=owner_token)


class GenerateOwnerTokenTests(TestCase):

    def test_generates_nonempty_reasonably_long_token(self):
        token = generate_owner_token()
        self.assertIsInstance(token, str)
        self.assertGreaterEqual(len(token), 32)

    def test_generates_unique_tokens(self):
        tokens = {generate_owner_token() for _ in range(50)}
        self.assertEqual(len(tokens), 50)


class IsOwnerTests(TestCase):

    def test_anonymous_with_correct_query_param_token_is_owner(self):
        job = _job(owner_token="secret-token")
        request = _request(token="secret-token")
        self.assertTrue(is_owner(request, job))

    def test_anonymous_with_correct_header_token_is_owner(self):
        job = _job(owner_token="secret-token")
        request = _request(header_token="secret-token")
        self.assertTrue(is_owner(request, job))

    def test_anonymous_with_wrong_token_is_not_owner(self):
        job = _job(owner_token="secret-token")
        request = _request(token="wrong-token")
        self.assertFalse(is_owner(request, job))

    def test_anonymous_with_no_token_is_not_owner(self):
        job = _job(owner_token="secret-token")
        request = _request()
        self.assertFalse(is_owner(request, job))

    def test_no_token_provided_and_none_set_on_job_is_not_owner(self):
        """An empty job.owner_token must never match an empty provided token."""
        job = _job(owner_token="")
        request = _request(token="")
        self.assertFalse(is_owner(request, job))

    def test_authenticated_owner_matches_without_any_token(self):
        user = User.objects.create_user(username="alice", password="x")
        job = _job(user=user)
        request = _request(user=user)
        self.assertTrue(is_owner(request, job))

    def test_authenticated_non_owner_is_denied_even_with_no_token_set(self):
        owner = User.objects.create_user(username="alice", password="x")
        other = User.objects.create_user(username="bob", password="x")
        job = _job(user=owner)
        request = _request(user=other)
        self.assertFalse(is_owner(request, job))

    def test_authenticated_non_owner_with_correct_token_still_succeeds(self):
        """The token is a valid access path for ANY holder, authenticated
        or not - by design, it's a bearer credential, not tied to a user
        row. An authenticated request that isn't job.user but does present
        the right token is legitimately the same case as an anonymous
        holder of that token."""
        owner = User.objects.create_user(username="alice", password="x")
        other = User.objects.create_user(username="bob", password="x")
        job = _job(user=owner, owner_token="secret-token")
        request = _request(user=other, token="secret-token")
        self.assertTrue(is_owner(request, job))


class ValidatePdfFileTests(TestCase):
    """
    validate_pdf_file() is shared by every PDF-upload feature (Merge,
    Split, Organize, ...) - covering these cases once here means every
    feature that reuses it is covered, rather than re-testing the same
    logic per app.
    """

    def test_accepts_a_real_pdf(self):
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()

        pdf = SimpleUploadedFile("doc.pdf", data, content_type="application/pdf")
        page_count = validate_pdf_file(pdf)
        self.assertEqual(page_count, 1)

    def test_rejects_encrypted_pdf(self):
        doc = fitz.open()
        doc.new_page()
        encrypted_bytes = doc.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner123", user_pw="user123",
        )
        doc.close()

        pdf = SimpleUploadedFile("encrypted.pdf", encrypted_bytes, content_type="application/pdf")
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf)

    def test_rejects_corrupted_pdf(self):
        # Valid signature, garbage body.
        pdf = SimpleUploadedFile("corrupt.pdf", b"%PDF-1.7\n%garbage not a real pdf structure", content_type="application/pdf")
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf)

    def test_rejects_fake_pdf_without_signature(self):
        pdf = SimpleUploadedFile("fake.pdf", b"just some text pretending to be a pdf", content_type="application/pdf")
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf)

    def test_rejects_empty_file(self):
        pdf = SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf)

    def test_rejects_non_pdf_extension(self):
        pdf = SimpleUploadedFile("doc.txt", b"%PDF-1.7 pretending", content_type="application/pdf")
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf)

    def test_rejects_oversized_file(self):
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()

        pdf = SimpleUploadedFile("doc.pdf", data, content_type="application/pdf")
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf, max_size=10)

    def test_rejects_pdf_with_too_many_pages(self):
        doc = fitz.open()
        for _ in range(5):
            doc.new_page()
        data = doc.tobytes()
        doc.close()

        pdf = SimpleUploadedFile("doc.pdf", data, content_type="application/pdf")
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf, max_pages=3)
