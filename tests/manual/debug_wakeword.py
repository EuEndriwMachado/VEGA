import time
import numpy as np
import sounddevice as sd
import openwakeword
from openwakeword.model import Model

def main():
    print("=" * 50)
    print("      DIAGNÓSTICO DE PALAVRA DE ATIVAÇÃO")
    print("=" * 50)
    print("\nBaixando/Verificando modelos padrão...")
    openwakeword.utils.download_models()

    print("\nIniciando escuta no microfone (Índice 1)...")
    print("Fale 'Hey Jarvis' no microfone e observe a pontuação abaixo:\n")

    # Carrega o modelo padrão 'hey_jarvis'
    oww_model = Model(
        wakeword_models=["hey_jarvis"],
        inference_framework="onnx"
    )

    def audio_callback(indata, frames, time_info, status):
        # Converte o sinal de áudio para int16
        audio_data = (indata[:, 0] * 32767).astype(np.int16)
        
        # Gera previsão
        prediction = oww_model.predict(audio_data)
        
        for model_name, score in prediction.items():
            # Exibe a pontuação se houver qualquer som captado
            if score > 0.05:
                barra = "█" * int(score * 30)
                print(f"[{model_name}] Pontuação: {score:.2f} | {barra}")

    try:
        # Usa 16000Hz padrão para o openWakeWord
        with sd.InputStream(
            device=1,
            channels=1,
            samplerate=16000,
            blocksize=1280,
            callback=audio_callback
        ):
            while True:
                time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nDiagnóstico encerrado.")
    except Exception as e:
        print(f"\nErro no microfone: {e}")

if __name__ == "__main__":
    main()