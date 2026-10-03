import os

from config.environment import load_vega_environment


load_vega_environment()


VEGA_NAME = "Vega"

VERSION = "0.1.0"

LANGUAGE = "pt-BR"

DEBUG = True

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
