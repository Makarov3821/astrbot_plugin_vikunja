import unittest

from plugin_loader import bootstrap_module as bootstrap


class FakeClient:
    """Minimal stand-in for VikunjaClient covering the bootstrap surface."""

    web_url = "https://vkj.example.com"

    def __init__(self):
        self.labels: list[dict] = []
        self.filters: list[dict] = []
        self.projects: list[dict] = []
        self.views: dict[int, list[dict]] = {}
        self.view_updates: list[tuple[int, int, dict]] = []
        self._next_id = 1

    def _id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    async def list_labels(self):
        return list(self.labels)

    async def create_label(self, title, hex_color=""):
        label = {"id": self._id(), "title": title, "hex_color": hex_color}
        self.labels.append(label)
        return label

    async def list_saved_filters(self):
        return list(self.filters)

    async def create_saved_filter(self, title, filter_query, **kwargs):
        filter_id = self._id()
        pseudo = bootstrap.project_id_from_saved_filter_id(filter_id)
        self.filters.append({"id": pseudo, "title": title, "filter": filter_query})
        self.views[pseudo] = [
            {"id": self._id(), "title": "Kanban", "view_kind": "kanban"},
            {"id": self._id(), "title": "Gantt", "view_kind": "gantt"},
        ]
        return {"id": filter_id, "title": title}

    async def list_views(self, project_id):
        return self.views.get(project_id, [])

    async def update_view(self, project_id, view_id, view):
        self.view_updates.append((project_id, view_id, view))
        for stored in self.views.get(project_id, []):
            if stored["id"] == view_id:
                stored.update(view)
        return view

    async def list_projects(self):
        return list(self.projects)

    async def create_project(self, title, parent_project_id=0, description=""):
        project = {
            "id": self._id(),
            "title": title,
            "parent_project_id": parent_project_id,
        }
        self.projects.append(project)
        return project


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_pseudo_project_id_mapping(self):
        # Vikunja: filter 1 is exposed as project -2.
        self.assertEqual(bootstrap.project_id_from_saved_filter_id(1), -2)
        self.assertEqual(bootstrap.saved_filter_id_from_project_id(-2), 1)

    async def test_first_run_creates_labels_filters_and_buckets(self):
        client = FakeClient()
        report = await bootstrap.apply(client, include_projects=True)

        self.assertEqual(len(client.labels), len(bootstrap.LABEL_SPECS))
        self.assertEqual(len(client.filters), len(bootstrap.FILTER_SPECS))
        titles = {item["title"] for item in client.filters}
        self.assertIn("🧭 总看板", titles)
        self.assertIn("📈 时间线", titles)

        # Project skeleton, including children.
        project_titles = {item["title"] for item in client.projects}
        self.assertIn("Inbox", project_titles)
        self.assertIn("课题一", project_titles)

        # The board kanban became filter driven and label names are numeric ids.
        self.assertEqual(len(client.view_updates), 1)
        _, _, view = client.view_updates[0]
        self.assertEqual(view["bucket_configuration_mode"], "filter")
        bucket_titles = [item["title"] for item in view["bucket_configuration"]]
        self.assertEqual(bucket_titles, ["等待中", "今天", "本周", "以后", "没排期"])
        waiting = view["bucket_configuration"][0]["filter"]["filter"]
        self.assertNotIn("@等待中", waiting)
        self.assertRegex(waiting, r"labels in \d+")
        self.assertTrue(
            view["bucket_configuration"][-1]["filter"]["filter_include_nulls"]
        )
        self.assertIn("https://vkj.example.com/projects/-", report.board_url)
        self.assertFalse(report.warnings)

    async def test_second_run_is_idempotent(self):
        client = FakeClient()
        await bootstrap.apply(client, include_projects=True)
        labels, filters, projects = (
            len(client.labels),
            len(client.filters),
            len(client.projects),
        )
        report = await bootstrap.apply(client, include_projects=True)
        self.assertEqual(len(client.labels), labels)
        self.assertEqual(len(client.filters), filters)
        self.assertEqual(len(client.projects), projects)
        self.assertFalse(report.created)
        self.assertTrue(report.skipped)
        # The kanban configuration is not rewritten either.
        self.assertEqual(len(client.view_updates), 1)

    async def test_board_links_and_guide_mention_every_filter(self):
        client = FakeClient()
        await bootstrap.apply(client)
        pseudo = {item["title"]: item["id"] for item in client.filters}
        links = bootstrap.board_links(client, pseudo)
        for spec in bootstrap.FILTER_SPECS:
            self.assertIn(spec.title, links)
        guide = bootstrap.structure_help()
        self.assertIn("due_date 只填真死线", guide)


if __name__ == "__main__":
    unittest.main()
