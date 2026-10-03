import os
import subprocess
import webbrowser
from datetime import datetime


def get_current_time():
    return datetime.now().strftime("%H:%M:%S")


def get_current_date():
    return datetime.now().strftime("%d/%m/%Y")


def open_website(url):
    webbrowser.open(url)
    return f"Abrindo {url}"


def open_program(program):
    try:
        subprocess.Popen(program, shell=True)
        return f"Abrindo {program}"
    except Exception as error:
        return f"Não consegui abrir {program}: {error}"


def open_folder(path):
    try:
        os.startfile(path)
        return f"Abrindo {path}"
    except Exception as error:
        return f"Não consegui abrir {path}: {error}"
