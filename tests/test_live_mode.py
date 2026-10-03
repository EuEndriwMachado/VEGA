import unittest

from core.brain.brain import VegaBrain


class LiveModeActivationTests(unittest.TestCase):
    def test_modo_live_is_recognized(self):
        brain = VegaBrain()
        response = brain._process_mode("modo live")
        self.assertIsNotNone(response)
        self.assertIn("live", response.lower())

    def test_desativa_modo_live_is_recognized(self):
        brain = VegaBrain()
        response = brain._process_mode("desativa o modo live")
        self.assertIsNotNone(response)
        self.assertIn("deslig", response.lower())


if __name__ == "__main__":
    unittest.main()
