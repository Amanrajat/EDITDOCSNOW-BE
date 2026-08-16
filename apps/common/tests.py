from types import SimpleNamespace
from unittest.mock import patch

import fitz
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers

from .ownership import generate_owner_token, is_owner
from .validation import validate_image_file, validate_pdf_file

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


class ValidateImageFileTests(TestCase):
    """
    validate_image_file() is shared by every image-upload feature
    (currently JPG-to-PDF) - same reasoning as ValidatePdfFileTests.
    """

    def _make_jpeg(self, name="photo.jpg", size=(100, 80)):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", size, color=(120, 30, 200)).save(buffer, format="JPEG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")

    def _make_png(self, name="photo.png", size=(100, 80)):
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", size, color=(10, 200, 30)).save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_accepts_a_real_jpeg(self):
        image = self._make_jpeg(size=(120, 90))
        width, height = validate_image_file(image)
        self.assertEqual((width, height), (120, 90))

    def test_accepts_a_real_png(self):
        image = self._make_png(size=(64, 48))
        width, height = validate_image_file(image)
        self.assertEqual((width, height), (64, 48))

    def test_rejects_fake_image_without_real_pixel_data(self):
        fake = SimpleUploadedFile("fake.jpg", b"just some text pretending to be a jpeg", content_type="image/jpeg")
        with self.assertRaises(serializers.ValidationError):
            validate_image_file(fake)

    def test_rejects_non_image_extension(self):
        image = self._make_jpeg(name="photo.gif")
        with self.assertRaises(serializers.ValidationError):
            validate_image_file(image)

    def test_rejects_oversized_image(self):
        image = self._make_jpeg()
        with self.assertRaises(serializers.ValidationError):
            validate_image_file(image, max_size=10)

    def test_rejects_a_pdf_disguised_as_a_jpg(self):
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        disguised = SimpleUploadedFile("doc.jpg", data, content_type="image/jpeg")
        with self.assertRaises(serializers.ValidationError):
            validate_image_file(disguised)


class HealthCheckTests(TestCase):
    """
    /health/ is what render.yaml's healthCheckPath polls to decide whether
    to route traffic to an instance at all - it must stay a plain 200
    regardless of DB/Redis/storage state, so a dependency being down
    surfaces as that feature failing, not this instance being killed.
    /health/?deep=true is the separate, opt-in diagnostic variant that
    actually contacts Postgres and Redis.
    """

    def test_returns_200_with_ok_status(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_deep_check_does_not_touch_redis_or_db_by_default(self):
        """The plain /health/ path must never import/contact redis at all -
        proven by patching it to always raise, which would fail this test
        if health_check's fast path accidentally called it."""
        with patch("apps.common.views._check_redis", side_effect=AssertionError("must not be called")):
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)

    def test_deep_check_reports_ok_when_dependencies_are_reachable(self):
        with patch("apps.common.views._check_redis", return_value=None):
            response = self.client.get("/health/?deep=true")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["checks"]["database"], "ok")
        self.assertEqual(body["checks"]["redis"], "ok")

    def test_deep_check_reports_degraded_when_redis_is_unreachable(self):
        with patch("apps.common.views._check_redis", side_effect=ConnectionError("Connection refused")):
            response = self.client.get("/health/?deep=true")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["checks"]["database"], "ok")
        self.assertIn("Connection refused", body["checks"]["redis"])

    def test_deep_check_reports_degraded_when_database_is_unreachable(self):
        with patch("apps.common.views._check_database", side_effect=Exception("no such database")):
            with patch("apps.common.views._check_redis", return_value=None):
                response = self.client.get("/health/?deep=true")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertIn("no such database", body["checks"]["database"])
        self.assertEqual(body["checks"]["redis"], "ok")
