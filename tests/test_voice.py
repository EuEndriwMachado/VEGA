import sounddevice as sd
import speech_recognition as sr
import numpy as np
import unittest


if __name__ != "__main__":
    raise unittest.SkipTest("Teste manual de voz; execute diretamente.")


MICROPHONE_INDEX = 1
SAMPLE_RATE = 16000
CHANNELS = 1


recognizer = sr.Recognizer()

# Ajustes para melhorar o reconhecimento
recognizer.energy_threshold = 300
recognizer.dynamic_energy_threshold = True
recognizer.pause_threshold = 0.8
recognizer.phrase_threshold = 0.3
recognizer.non_speaking_duration = 0.5


print("=" * 40)
print("          TESTE DE VOZ DA VEGA")
print("=" * 40)
print()
print("Microfone:", MICROPHONE_INDEX)
print("Calibrando...")
print()


# ----------------------------------------------------------
# CALIBRAÇÃO
# ----------------------------------------------------------

print("Fique em silêncio por 2 segundos...")

calibration = sd.rec(
    int(SAMPLE_RATE * 2),
    samplerate=SAMPLE_RATE,
    channels=CHANNELS,
    dtype="int16",
    device=MICROPHONE_INDEX
)

sd.wait()

audio_data = np.frombuffer(
    calibration.tobytes(),
    dtype=np.int16
)

noise_level = np.mean(
    np.abs(audio_data)
)

print(
    f"Nível de ruído: {noise_level:.2f}"
)

print()
print("Agora fale.")
print()


# ----------------------------------------------------------
# CAPTURA
# ----------------------------------------------------------

duration = 5

recording = sd.rec(
    int(SAMPLE_RATE * duration),
    samplerate=SAMPLE_RATE,
    channels=CHANNELS,
    dtype="int16",
    device=MICROPHONE_INDEX
)

sd.wait()


# ----------------------------------------------------------
# CONVERTER PARA SPEECH_RECOGNITION
# ----------------------------------------------------------

audio_bytes = recording.tobytes()

audio = sr.AudioData(
    audio_bytes,
    SAMPLE_RATE,
    2
)


# ----------------------------------------------------------
# RECONHECIMENTO
# ----------------------------------------------------------

try:

    print("Reconhecendo...")

    text = recognizer.recognize_google(
        audio,
        language="pt-BR"
    )

    print()
    print("VEGA ouviu:")
    print(text)
    print()

except sr.UnknownValueError:

    print()
    print("Não consegui entender o que foi dito.")

except sr.RequestError as error:

    print()
    print(
        f"Erro no serviço de reconhecimento: {error}"
    )

except Exception as error:

    print()
    print(
        f"Erro: {error}"
    )
