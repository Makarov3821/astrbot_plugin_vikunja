import unittest

from vikunja import normalize_api_url


class VikunjaClientTests(unittest.TestCase):
    def test_normalizes_site_url(self):
        self.assertEqual(normalize_api_url("https://todo.example.com/"), "https://todo.example.com/api/v1")

    def test_preserves_api_url(self):
        self.assertEqual(normalize_api_url("https://todo.example.com/api/v1"), "https://todo.example.com/api/v1")

    def test_rejects_relative_url(self):
        with self.assertRaises(ValueError):
            normalize_api_url("todo.example.com")


if __name__ == "__main__":
    unittest.main()

