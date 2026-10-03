import os
import time
from core.audio.activation import ActivationManager


def ao_ativar():

    print("\n" + "=" * 50)
    print("   [!] VEGA ATIVADA COM SUCESSO!")
    print("   (Gatilho detectado: Palmas duplas ou Palavra de Ativação)")
    print("=" * 50 + "\n")


def main():

    print("=" * 50)
    print("       TESTE DE ATIVAÇÃO DA VEGA")
    print("=" * 50)
    print()
    print("Gatilhos ativos:")
    print(" 1. Palmas duplas (duas palmas aceleradas)")
    print(" 2. Palavras de ativação: 'Acorda Vega', 'Ei Vega', 'Vega'")
    print()
    print("Pressione CTRL+C para encerrar o teste.")
    print("-" * 50)
    print()

    # Verifica quais modelos customizados existem na pasta raiz
    modelos_custom = ["acorda_vega.onnx", "ei_vega.onnx", "vega.onnx"]
    modelos_encontrados = [m for m in modelos_custom if os.path.exists(m)]

    if modelos_encontrados:

        modelos_para_usar = modelos_encontrados

        print(
            f"[INFO] Carregando modelos customizados: {modelos_encontrados}\n"
        )

    else:

        # Fallback de segurança para o teste de voz caso os arquivos .onnx ainda não estejam na pasta
        modelos_para_usar = ["hey_jarvis"]

        print(
            "[AVISO] Arquivos .onnx customizados não foram encontrados no diretório."
        )
        print(
            "[AVISO] Usando modelo padrão ('hey_jarvis') para o teste de voz."
        )
        print(
            "[AVISO] O detector de palmas está 100% ativo!\n"
        )

    # Inicializa o gerenciador unificado
    manager = ActivationManager(
        wake_models=modelos_para_usar,
        on_trigger_callback=ao_ativar
    )

    # Inicia a escuta nos detectores
    manager.start(device_index=1)

    try:

        while True:

            time.sleep(0.5)

    except KeyboardInterrupt:

        print("\nEncerrando detectores...")

        manager.stop()

        print("Teste finalizado com sucesso.")


if __name__ == "__main__":
    main()