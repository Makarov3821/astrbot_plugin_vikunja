import unittest

from state_store import StateStore


class StateStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_and_dedup_are_persisted(self):
        persisted = {}

        async def load():
            return persisted

        async def save(value):
            persisted.clear()
            persisted.update(value)

        store = StateStore(load, save)
        await store.initialize()
        await store.register_channel(
            "qq-private", {"umo": "qq-private", "reminders_enabled": True}
        )
        await store.set_task_reminder(12, 60)
        await store.mark_sent("qq-private|12|due", "2026-07-11T00:00:00Z")

        self.assertEqual(store.channel("qq-private")["umo"], "qq-private")
        self.assertEqual(store.task_reminder_minutes(12, 30), 60)
        self.assertTrue(store.was_sent("qq-private|12|due"))
        self.assertIn("channels", persisted)


if __name__ == "__main__":
    unittest.main()
