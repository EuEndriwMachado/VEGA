import marshal
import dis
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def disassemble_method(name):
    with open('config/__pycache__/interface.cpython-312.pyc', 'rb') as f:
        f.seek(16)
        code = marshal.load(f)

    iface = [c for c in code.co_consts if getattr(c, 'co_name', '') == 'InterfaceVEGA'][0]
    methods = {c.co_name: c for c in iface.co_consts if hasattr(c, 'co_name')}
    
    if name not in methods:
        print(f"Method {name} not found")
        return
    m = methods[name]
    print(f"=== {name} ===")
    dis.dis(m)

if __name__ == '__main__':
    for arg in sys.argv[1:]:
        disassemble_method(arg)
