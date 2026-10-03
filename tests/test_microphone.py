import sounddevice as sd
import numpy as np
import unittest


if __name__ != "__main__":
    raise unittest.SkipTest("Teste manual de microfone; execute diretamente.")


SAMPLE_RATE = 16000
DURATION = 5


print("=" * 50)
print("             TESTE DO MICROFONE")
print("=" * 50)
print()

print("Microfones encontrados:")
print()

devices = sd.query_devices()

for index, device in enumerate(devices):

    if device["max_input_channels"] > 0:

        print(
            f"{index}: {device['name']}"
        )

print()
print("Gravando por 5 segundos...")
print("Fale normalmente.")
print()

audio = sd.rec(
    int(DURATION * SAMPLE_RATE),
    samplerate=SAMPLE_RATE,
    channels=1,
    dtype="float32"
)

sd.wait()

audio = audio.flatten()

volume = np.max(np.abs(audio))

print()
print("Gravação finalizada.")
print()

print(
    f"Nível máximo capturado: {volume:.4f}"
)

if volume < 0.01:

    print()
    print("⚠️ Não detectei som suficiente.")
    print("Verifique o microfone e tente novamente.")

else:

    print()
    print("✅ Microfone funcionando!")
    print("A VEGA conseguiu capturar áudio.")
