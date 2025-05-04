import json
import os

class ConfigManager:
    def __init__(self, file="config.json"):
        self.file = file

    def load_config(self):
        if not os.path.exists(self.file):
            raise FileNotFoundError("config.json not found")
        with open(self.file, "r") as f:
            return json.load(f)
