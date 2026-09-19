import unittest

from vikunja import normalize_api_url, web_base_url


class VikunjaClientTests(unittest.TestCase):
    def test_normalizes_site_url(self):
        self.assertEqual(
            normalize_api_url("https://todo.example.com/"),
            "https://todo.example.com/api/v1",
        )

    def test_preserves_api_url(self):
        self.assertEqual(
            normalize_api_url("https://todo.example.com/api/v1"),
            "https://todo.example.com/api/v1",
        )

    def test_rejects_relative_url(self):
        with self.assertRaises(ValueError):
            normalize_api_url("todo.example.com")

    def test_board_links_use_the_browser_url(self):
        api = normalize_api_url("https://vkj.example.com/")
        self.assertEqual(web_base_url(api), "https://vkj.example.com")
        self.assertEqual(
            web_base_url(normalize_api_url("https://host/vikunja")),
            "https://host/vikunja",
        )
        self.assertEqual(web_base_url(""), "")


if __name__ == "__main__":
    unittest.main()
