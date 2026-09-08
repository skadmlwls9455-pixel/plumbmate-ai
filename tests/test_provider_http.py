import io
import unittest
import urllib.error
from unittest import mock

from plumbmate.provider_http import MAX_PROVIDER_BYTES, _NoRedirect, post_openai
from plumbmate.protocol import ProtocolError


class HTTPTests(unittest.TestCase):
    def test_provider_body_read_is_bounded(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"x" * (MAX_PROVIDER_BYTES + 1)
        with mock.patch("urllib.request.OpenerDirector.open", return_value=response), self.assertRaises(ProtocolError):
            post_openai("/responses", {}, "fake")
        response.read.assert_called_once_with(MAX_PROVIDER_BYTES + 1)

    def test_http_failure_never_echoes_private_remote_body(self):
        error = urllib.error.HTTPError("https://api.openai.com", 400, "private", {}, io.BytesIO(b"PRIVATE_CONTENT"))
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=error), self.assertRaises(ProtocolError) as caught:
            post_openai("/responses", {}, "fake")
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertTrue(error.fp.closed)

    def test_unapproved_endpoint_and_redirect_refused(self):
        with mock.patch("urllib.request.OpenerDirector.open") as http, self.assertRaises(ProtocolError):
            post_openai("https://elsewhere.invalid", {}, "fake")
        http.assert_not_called()
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, None, None, "https://elsewhere.invalid"))
