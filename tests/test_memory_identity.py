import os
import tempfile
import unittest

from core.memory.memory import VegaMemory


class MemoryIdentityTests(unittest.TestCase):
    def test_clear_all_and_seed_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = VegaMemory(base_path=tmpdir)

            memory.remember("interesse", "musica")
            memory.add_daily_message("user", "oi")

            memory.clear_all()
            self.assertEqual(memory.get_permanent(), {})
            self.assertEqual(memory.get_daily(), [])

            memory.seed_identity()
            permanent = memory.get_permanent()

            self.assertIn("vega", permanent)
            self.assertIn("acau", permanent)
            self.assertIn("criador da vega", permanent)
            self.assertIn("origem do nome vega", permanent)
            self.assertIn("significado simbolico do nome vega", permanent)
            self.assertIn("limites da vega", permanent)


if __name__ == "__main__":
    unittest.main()
