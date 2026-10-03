"""Diagn�stico: separar crash do Qt vs do c�digo da interface."""
import sys, traceback

print("PASSO 1: importar PyQt", flush=True)
from PySide6.QtWidgets import QApplication
print("PASSO 2: criar QApplication", flush=True)
app = QApplication.instance() or QApplication(sys.argv)
print("PASSO 3: QApplication ok", flush=True)
try:
    print("PASSO 4: importar config.interface", flush=True)
    from config.interface import InterfaceVEGA
    print("PASSO 5: instanciar InterfaceVEGA", flush=True)
    iface = InterfaceVEGA()
    print("PASSO 6: instancia OK", flush=True)
    import time
    for i in range(3):
        time.sleep(0.5)
        print(f"tick {i}", flush=True)
    iface.show()
    iface.show()
    print("PASSO 7: show OK", flush=True)
except BaseException:
    traceback.print_exc()
print("FIM", flush=True)
